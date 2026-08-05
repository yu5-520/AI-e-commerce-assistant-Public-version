from __future__ import annotations

from pathlib import Path

from src.services.agent_execution_lock_v2255_service import (
    EVIDENCE_CONFLICT,
    EVIDENCE_INSUFFICIENT,
    EXECUTION_LOCK_HOTFIX_VERSION,
    execution_lock_from,
    missing_execution_lock,
)

ROOT = Path(__file__).resolve().parents[1]


def _base(**overrides: object) -> dict:
    value = {
        "productId": "P10009",
        "decisionType": "act",
        "selectedOperatingRoute": "title_image_test",
        "selectedActionFamilyHint": "title_image_test",
        "primaryProblemNode": "点击率连续下降",
        "primaryAction": "执行一轮标题与主图低风险A/B测试",
        "primaryOwner": "运营",
        "primaryExecutionTarget": "商品标题与主图素材",
        "decisiveFacts": [
            "点击率连续三期同向下降",
            "曝光量保持稳定",
        ],
        "evidenceStatus": EVIDENCE_INSUFFICIENT,
        "missingEvidence": [
            "具体的竞品素材对比数据",
            "不同创意单元的历史CTR细分数据",
        ],
    }
    value.update(overrides)
    return value


def test_string_target_and_title_image_family_form_a_reversible_lock() -> None:
    lock = execution_lock_from(_base())
    assert EXECUTION_LOCK_HOTFIX_VERSION == "22.5.13"
    assert lock["locked"] is True
    assert lock["evidenceStatus"] == "sufficient"
    assert lock["evidenceBasis"] == "cross_validated_reversible_test"
    assert lock["riskClass"] == "reversible_test"
    assert lock["primaryExecutionTarget"]["targetId"] == "P10009"
    assert lock["primaryExecutionTarget"]["targetType"]
    assert lock["reviewRequired"] is True
    assert lock["rollbackRequired"] is True
    assert missing_execution_lock(lock) == []


def test_missing_target_type_is_deterministically_mapped_for_roas_scale() -> None:
    lock = execution_lock_from(
        _base(
            productId="P10003",
            selectedOperatingRoute="traffic_scaling",
            selectedActionFamilyHint="roas_scale",
            primaryProblemNode="高ROI商品的投放规模低于可承接空间",
            primaryAction="在现有权限内小幅扩量并设置复盘点",
            primaryExecutionTarget={"targetId": "PLAN-P10003"},
            decisiveFacts=[
                "ROI连续三期高于目标",
                "预算消耗与转化保持稳定",
            ],
            missingEvidence=["Competitor activity data."],
        )
    )
    assert lock["locked"] is True
    assert lock["primaryExecutionTarget"]["targetType"] == (
        "product_ad_plan_scope"
    )
    assert lock["primaryExecutionTarget"]["targetId"] == "PLAN-P10003"
    assert lock["advisoryMissingEvidence"] == [
        "Competitor activity data."
    ]
    assert missing_execution_lock(lock) == []


def test_budget_permission_gap_remains_a_hard_blocker() -> None:
    lock = execution_lock_from(
        _base(
            selectedOperatingRoute="traffic_scaling",
            selectedActionFamilyHint="roas_scale",
            primaryExecutionTarget={"targetId": "PLAN-P10003"},
            missingEvidence=["预算权限未确认"],
        )
    )
    assert lock["locked"] is False
    assert lock["evidenceStatus"] == EVIDENCE_INSUFFICIENT
    assert "预算权限未确认" in lock["hardEvidenceBlockers"]
    assert "executionLock.evidenceStatus_sufficient" in (
        missing_execution_lock(lock)
    )


def test_profit_boundary_gap_remains_a_hard_blocker() -> None:
    lock = execution_lock_from(
        _base(
            selectedOperatingRoute="traffic_scaling",
            selectedActionFamilyHint="roas_scale",
            primaryExecutionTarget={"targetId": "PLAN-P10003"},
            missingEvidence=["商品毛利率与成本边界缺失"],
        )
    )
    assert lock["locked"] is False
    assert "商品毛利率与成本边界缺失" in (
        lock["hardEvidenceBlockers"]
    )


def test_evidence_conflict_is_never_promoted() -> None:
    lock = execution_lock_from(
        _base(
            evidenceStatus=EVIDENCE_CONFLICT,
            missingEvidence=["证据冲突"],
        )
    )
    assert lock["locked"] is False
    assert lock["evidenceStatus"] == EVIDENCE_CONFLICT


def test_unknown_family_keeps_the_strict_execution_lock() -> None:
    lock = execution_lock_from(
        _base(
            selectedOperatingRoute="unknown_route",
            selectedActionFamilyHint="unknown_family",
            primaryExecutionTarget={"targetId": "P10009"},
            missingEvidence=["辅助资料缺失"],
        )
    )
    assert lock["locked"] is False
    assert lock["evidenceStatus"] == EVIDENCE_INSUFFICIENT


def test_existing_sufficient_irreversible_lock_still_passes_without_promotion() -> None:
    lock = execution_lock_from(
        _base(
            selectedOperatingRoute="strict_delist",
            selectedActionFamilyHint="product_delist",
            primaryExecutionTarget={
                "targetType": "product_listing",
                "targetId": "P10009",
            },
            evidenceStatus="sufficient",
            missingEvidence=[],
        )
    )
    assert lock["locked"] is True
    assert lock["evidenceStatus"] == "sufficient"
    assert lock.get("evidenceBasis") != (
        "cross_validated_reversible_test"
    )


def test_recovery_module_never_calls_provider_or_mutates_execution_index() -> None:
    source = (
        ROOT
        / "src/services/recover_agent1_execution_lock_v22513_service.py"
    ).read_text(encoding="utf-8")
    assert "call_json" not in source
    assert "llm_gateway" not in source
    assert "providerCallsExecuted" in source
    assert '"providerCallsExecuted": 0' in source
    assert "UPDATE artifact_execution_index_v2259" not in source
    assert "DELETE FROM artifact_execution_index_v2259" not in source
    assert "nativeObservationsTouched" in source
    assert '"nativeObservationsTouched": False' in source
