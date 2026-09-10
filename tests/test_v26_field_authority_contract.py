from __future__ import annotations

import pytest

from src.services.v26_field_authority_contract_service import (
    FieldAuthorityViolation,
    V26FieldAuthorityContract,
)


def authority() -> V26FieldAuthorityContract:
    return V26FieldAuthorityContract()


def test_semantic_field_checks_header_not_business_wording() -> None:
    guard = authority()
    text = (
        "将日预算调整至方案批准值，并解释为什么目标 ROAS 与活动节奏需要一起观察；"
        "increase budget only when the approved plan reference permits it."
    )
    policy = guard.assert_write("agent3", "operation.step_instruction", text)
    assert policy["class"] == "SEMANTIC"
    assert guard.receipt()["semanticBusinessWordingInspected"] is False


def test_agent3_cannot_write_agent2_plan_number() -> None:
    with pytest.raises(FieldAuthorityViolation, match="v26_field_write_forbidden"):
        authority().assert_write("agent3", "plan.daily_budget", 550)


def test_agent2_can_write_plan_number_but_not_snapshot_or_derived() -> None:
    guard = authority()
    guard.assert_write("agent2", "plan.daily_budget", 550)
    guard.assert_write("agent2", "plan.target_roas", 3.2)
    guard.assert_write("agent2", "plan.review_window", "72h")

    with pytest.raises(FieldAuthorityViolation, match="v26_field_write_forbidden"):
        guard.assert_write("agent2", "snapshot.roas", 9.9)
    with pytest.raises(FieldAuthorityViolation, match="v26_field_write_forbidden"):
        guard.assert_write("agent2", "derived.rolling_roas", 9.9)


def test_agent1_owns_judgement_numeric_not_plan_numeric() -> None:
    guard = authority()
    guard.assert_write("agent1", "judgement.priority_weight", 0.82)
    guard.assert_write("agent1", "judgement.evidence_confidence", 0.74)
    with pytest.raises(FieldAuthorityViolation, match="v26_field_write_forbidden"):
        guard.assert_write("agent1", "plan.daily_budget", 300)
    with pytest.raises(FieldAuthorityViolation, match="v26_field_above_maximum"):
        guard.assert_write("agent1", "judgement.priority_weight", 1.2)


def test_agent3_plan_values_are_referenced_not_reauthored() -> None:
    guard = authority()
    guard.assert_write(
        "agent3",
        "operation.plan_refs",
        ["plan.daily_budget", "plan.target_roas", "plan.review_window"],
    )
    with pytest.raises(FieldAuthorityViolation, match="v26_reference_namespace_mismatch"):
        guard.assert_write("agent3", "operation.plan_refs", ["snapshot.roas"])


def test_exact_header_registration_is_fail_closed() -> None:
    guard = authority()
    with pytest.raises(FieldAuthorityViolation, match="v26_field_header_unregistered"):
        guard.assert_write("agent2", "plan.unregistered_amount", 100)
    with pytest.raises(FieldAuthorityViolation, match="v26_field_header_unregistered"):
        guard.assert_write("agent3", "operation.unregistered_instruction", "do it")


def test_system_stage_and_operation_stage_have_different_authority() -> None:
    guard = authority()
    with pytest.raises(FieldAuthorityViolation, match="v26_system_stage_write_forbidden"):
        guard.assert_stage_write("agent3", "systemStage")
    guard.assert_stage_write("java-control-plane", "systemStage")
    guard.assert_stage_write("agent3", "operationStage")
    with pytest.raises(FieldAuthorityViolation, match="v26_operation_stage_write_forbidden"):
        guard.assert_stage_write("agent2", "operationStage")


def test_registered_stage_headers_follow_same_authority() -> None:
    guard = authority()
    with pytest.raises(FieldAuthorityViolation, match="v26_field_write_forbidden"):
        guard.assert_write("agent3", "system_stage.call_graph", ["agent1", "agent2", "agent3"])
    guard.assert_write("agent3", "operation_stage.stage_id", "STAGE-1")
    guard.assert_write("agent3", "operation_stage.stage_name", "标题与主图准备")
