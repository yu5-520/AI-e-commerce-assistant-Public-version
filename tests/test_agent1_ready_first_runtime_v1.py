from __future__ import annotations

from src.services import agent_runtime_hard_interface_v22515_service as runtime


def _candidate(item_id: str) -> dict:
    return {"item_id": item_id}


def _envelope(chars: int) -> dict:
    return {
        "schema": "agent_input.agent1.v3",
        "projectionAudit": {"projectedChars": chars},
        "payload": {"productId": "P"},
    }


def test_less_than_capacity_runs_immediately(monkeypatch):
    items = [_candidate("I1"), _candidate("I2"), _candidate("I3")]
    monkeypatch.setattr(runtime.pipeline_agent1_core, "_pending_items", lambda dv, limit: items[:limit])
    monkeypatch.setattr(runtime.pipeline_agent1_core, "pending_agent1_item_count", lambda dv: 3)
    monkeypatch.setattr(runtime, "build_operating_policy_context", lambda: {})
    monkeypatch.setattr(runtime, "ensure_agent1_input_ref", lambda item, policy_context: f"ART-{item['item_id']}")
    monkeypatch.setattr(runtime, "resolve_agent_input_ref", lambda ref, expected_schema: _envelope(10_000))

    plan = runtime.plan_agent1_ready_first_batch("DV-1", batch_size=8)

    assert plan["capacityItems"] == 8
    assert plan["candidateWindowCount"] == 3
    assert plan["selectedItemCount"] == 3
    assert plan["remainingReadyCount"] == 0
    assert plan["waitForFullCapacity"] is False
    assert plan["providerCallsExecuted"] == 0


def test_char_budget_selects_only_first_provider_subbatch(monkeypatch):
    items = [_candidate(f"I{i}") for i in range(1, 6)]
    chars_by_ref = {
        "ART-I1": 20_000,
        "ART-I2": 20_000,
        "ART-I3": 20_000,
        "ART-I4": 20_000,
        "ART-I5": 5_000,
    }
    monkeypatch.setattr(runtime.pipeline_agent1_core, "_pending_items", lambda dv, limit: items[:limit])
    monkeypatch.setattr(runtime.pipeline_agent1_core, "pending_agent1_item_count", lambda dv: 5)
    monkeypatch.setattr(runtime, "build_operating_policy_context", lambda: {})
    monkeypatch.setattr(runtime, "ensure_agent1_input_ref", lambda item, policy_context: f"ART-{item['item_id']}")
    monkeypatch.setattr(
        runtime,
        "resolve_agent_input_ref",
        lambda ref, expected_schema: _envelope(chars_by_ref[ref]),
    )

    plan = runtime.plan_agent1_ready_first_batch("DV-2", batch_size=8)

    assert plan["selectedItemCount"] == 3
    assert plan["selectedProjectedChars"] == 60_000
    assert plan["selectedItemIds"] == ["I1", "I2", "I3"]
    assert plan["remainingReadyCount"] == 2
    assert plan["inspectedPrefix"][-1]["stopReason"] == "batch_char_budget_would_be_exceeded"


def test_wrapper_passes_selected_prefix_size_to_existing_hard_runtime(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        runtime,
        "plan_agent1_ready_first_batch",
        lambda data_version, batch_size: {
            "selectedItemCount": 3,
            "policy": runtime.AGENT1_READY_FIRST_POLICY,
        },
    )

    def _legacy(data_version, *, user_id=None, batch_size=8):
        captured["dataVersion"] = data_version
        captured["batchSize"] = batch_size
        return {"ran": True, "claimedItemCount": batch_size}

    monkeypatch.setattr(runtime.legacy, "run_agent1_microbatch_hard", _legacy)

    result = runtime.run_agent1_ready_first_microbatch_hard("DV-3", batch_size=8)

    assert captured == {"dataVersion": "DV-3", "batchSize": 3}
    assert result["effectiveClaimBatchSize"] == 3
    assert result["configuredBatchCapacity"] == 8
    assert result["claimScope"] == "current_provider_subbatch_only"
    assert result["secondWorkerCreated"] is False
    assert result["providerConfigurationChanged"] is False
    assert result["exactHashRuntimeChanged"] is False


def test_prepare_failure_stays_in_selected_prefix(monkeypatch):
    items = [_candidate("BAD"), _candidate("GOOD")]
    monkeypatch.setattr(runtime.pipeline_agent1_core, "_pending_items", lambda dv, limit: items[:limit])
    monkeypatch.setattr(runtime.pipeline_agent1_core, "pending_agent1_item_count", lambda dv: 2)
    monkeypatch.setattr(runtime, "build_operating_policy_context", lambda: {})

    def _ensure(item, policy_context):
        if item["item_id"] == "BAD":
            raise RuntimeError("projection_failed")
        return "ART-GOOD"

    monkeypatch.setattr(runtime, "ensure_agent1_input_ref", _ensure)
    monkeypatch.setattr(runtime, "resolve_agent_input_ref", lambda ref, expected_schema: _envelope(10_000))

    plan = runtime.plan_agent1_ready_first_batch("DV-4", batch_size=8)

    assert plan["selectedItemCount"] == 2
    assert plan["prepareFailureItemIds"] == ["BAD"]
    assert plan["selectedItemIds"] == ["BAD", "GOOD"]


def test_ready_first_does_not_replace_registered_agent1_stage_owner():
    binding = runtime.active_agent1_runtime_binding()

    assert binding["matched"] is True
    assert binding["agent1StageOwner"] == "src.services.agent_runtime_hard_interface_v2257_service"
    assert binding["tokenRuntimeOwner"] == "src.services.agent_token_runtime_hash_exact_v2259_service"
    assert binding["agent1ReadyFirstRuntimeVersion"] == "23.1.5"
    assert binding["secondWorkerCreated"] is False
