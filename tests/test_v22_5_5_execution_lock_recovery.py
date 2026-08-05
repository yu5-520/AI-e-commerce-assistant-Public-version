from __future__ import annotations

from src.services.agent_execution_lock_recovery_v2255_service import _eligible


def test_recovery_rejects_observed_and_unrelated_failures() -> None:
    observed = {
        "current_stage": "observed_soft_gate",
        "last_error_code": None,
        "payload": None,
    }
    ok, reason = _eligible(observed, {"agent1InputRef": "ART-A1"})
    assert ok is False
    assert reason["reason"] == "stage_not_eligible"

    unrelated = {
        "current_stage": "agent2_draft_output_invalid",
        "last_error_code": "provider_timeout",
        "payload": {"draftStatus": "draft_conflict"},
    }
    ok, reason = _eligible(unrelated, {"agent1InputRef": "ART-A1"})
    assert ok is False
    assert reason["reason"] == "not_business_missing_data"


def test_recovery_requires_preserved_agent1_input_ref() -> None:
    row = {
        "current_stage": "agent2_draft_output_invalid",
        "payload": {"draftStatus": "draft_missing_data"},
    }
    ok, reason = _eligible(row, {})
    assert ok is False
    assert reason["reason"] == "agent1_input_ref_missing"
