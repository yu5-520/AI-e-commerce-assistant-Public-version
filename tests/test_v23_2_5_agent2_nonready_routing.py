from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services import agent2_action_draft_core_v225_service as core
from src.services import agent2_runtime_v22515_service as runtime


def _base_draft(status: str, **extra):
    draft = {
        "packageId": "PKG-1",
        "productId": "P10001",
        "storeId": "DY-SH-003",
        "actionFamily": "title_image_test",
        "draftStatus": status,
        "primaryProblemNode": "creative_ctr_drop",
        "primaryAction": "title_image_test",
        "primaryExecutionTarget": {"type": "product", "id": "P10001"},
        "primaryOwner": "operator",
        "executionTargets": [{"type": "product", "id": "P10001"}],
        "compoundActionRejected": False,
        "primaryTargetMutationRejected": False,
        "fallbackAllowed": False,
    }
    draft.update(extra)
    return draft


def _patch_normalizer_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = {
        "selectedActionFamily": "title_image_test",
        "primaryProblemNode": "creative_ctr_drop",
        "primaryAction": "title_image_test",
        "primaryExecutionTarget": {"type": "product", "id": "P10001"},
        "primaryOwner": "operator",
        "supportingCoordination": [],
        "forbiddenActionDomains": [],
    }
    monkeypatch.setattr(core, "selected_family", lambda package: "title_image_test")
    monkeypatch.setattr(core, "execution_lock_from", lambda package: lock)
    monkeypatch.setattr(core, "missing_execution_lock", lambda value: [])
    monkeypatch.setattr(core, "_normalize_family_draft", lambda raw, family, value: {})


def test_nonready_contract_requires_explicit_business_reason() -> None:
    missing = core.missing_agent2_draft_contract(
        _base_draft(core.DRAFT_MISSING_DATA, missingData=[])
    )
    assert "agent2_missing_data_reason_missing" in missing
    assert "agent2_missing_data_reason_missing" not in core.missing_agent2_draft_contract(
        _base_draft(core.DRAFT_MISSING_DATA, missingData=["缺少最近5份主图点击率"])
    )

    missing = core.missing_agent2_draft_contract(
        _base_draft(core.DRAFT_CONFLICT, conflictReasons=[])
    )
    assert "agent2_conflict_reason_missing" in missing
    assert "agent2_conflict_reason_missing" not in core.missing_agent2_draft_contract(
        _base_draft(core.DRAFT_CONFLICT, conflictReasons=["执行锁对象与报表商品不一致"])
    )

    missing = core.missing_agent2_draft_contract(
        _base_draft(core.DRAFT_REJECTED, rejectedReason="")
    )
    assert "agent2_rejected_reason_missing" in missing
    assert "agent2_rejected_reason_missing" not in core.missing_agent2_draft_contract(
        _base_draft(core.DRAFT_REJECTED, rejectedReason="动作超出运营权限边界")
    )


def test_normalizer_preserves_conflict_and_rejection_reason_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_normalizer_lock(monkeypatch)

    conflict = core._normalize_draft(
        {
            "packageId": "PKG-1",
            "productId": "P10001",
            "storeId": "DY-SH-003",
            "draftStatus": "draft_conflict",
            "conflicts": ["执行锁与事实冲突"],
        },
        {},
    )
    assert conflict["draftStatus"] == core.DRAFT_CONFLICT
    assert conflict["conflictReasons"] == ["执行锁与事实冲突"]
    assert "agent2_conflict_reason_missing" not in conflict["semanticContractMissing"]

    rejected = core._normalize_draft(
        {
            "packageId": "PKG-1",
            "productId": "P10001",
            "storeId": "DY-SH-003",
            "draftStatus": "draft_rejected",
            "rejectionReason": "动作超过权限",
        },
        {},
    )
    assert rejected["draftStatus"] == core.DRAFT_REJECTED
    assert rejected["rejectedReason"] == "动作超过权限"
    assert "agent2_rejected_reason_missing" not in rejected["semanticContractMissing"]


def test_incomplete_declared_ready_draft_is_not_promoted_without_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_normalizer_lock(monkeypatch)
    draft = core._normalize_draft(
        {
            "packageId": "PKG-1",
            "productId": "P10001",
            "storeId": "DY-SH-003",
            "draftStatus": "draft_ready",
        },
        {},
    )
    assert draft["draftStatus"] == ""
    assert draft["modelDeclaredDraftStatus"] == core.DRAFT_READY
    assert draft["systemComputedDraftStatus"] is True
    assert "agent2_output_channel_missing" in draft["semanticContractMissing"]
    assert "agent2_title_image_creative_draft_missing" in draft["semanticContractMissing"]


@pytest.mark.parametrize(
    ("status", "expected_stage", "detail_field", "detail_value"),
    [
        (
            core.DRAFT_MISSING_DATA,
            "agent2_missing_data_hold",
            "missingData",
            ["缺少最近5份主图点击率"],
        ),
        (
            core.DRAFT_CONFLICT,
            "agent2_conflict_hold",
            "conflictReasons",
            ["执行锁对象冲突"],
        ),
        (
            core.DRAFT_REJECTED,
            "agent2_rejected_hold",
            "rejectedReason",
            "动作超出权限",
        ),
    ],
)
def test_valid_nonready_result_routes_to_blocked_hold_without_provider_or_task_admission(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_stage: str,
    detail_field: str,
    detail_value,
) -> None:
    calls = []
    attached = []
    cleared = []

    def finish_item(item, **kwargs):
        calls.append((item, kwargs))
        return {"payloadArtifactRef": "ART-HOLD-1"}

    worker = SimpleNamespace(_finish_item=finish_item)
    monkeypatch.setattr(
        runtime,
        "attach_pipeline_artifact_ref",
        lambda item_id, key, ref: attached.append((item_id, key, ref)),
    )
    monkeypatch.setattr(runtime, "_clear_failure_state", lambda item_id: cleared.append(item_id))

    draft = _base_draft(status, **{detail_field: detail_value})
    candidate = {
        "packageId": "PKG-1",
        "agent2ActionDraft": draft,
        "agent2DraftExecutionProof": {
            "resultMatched": True,
            "providerCallExecuted": True,
            "fallbackUsed": False,
            "semanticCallId": "CALL-1",
        },
        "taskAdmissionAllowed": False,
        "fallbackAllowed": False,
        "lineage": {"currentStage": "agent2_draft_ready"},
    }
    item = {
        "item_id": "PI-1",
        "package_id": "PKG-1",
        "data_version": "DV-1",
    }

    runtime._finish_hold(worker, item, candidate, draft)

    assert len(calls) == 1
    kwargs = calls[0][1]
    assert kwargs["stage"] == expected_stage
    assert kwargs["status"] == "blocked"
    assert kwargs["payload"]["taskAdmissionAllowed"] is False
    assert kwargs["payload"]["fallbackAllowed"] is False
    assert kwargs["payload"]["lineage"]["currentStage"] == expected_stage
    assert kwargs["payload"]["holdDetail"] == detail_value
    assert "providerCall" not in kwargs["payload"]
    assert attached == [("PI-1", "agent2DraftRef", "ART-HOLD-1")]
    assert cleared == ["PI-1"]


def test_precise_nonready_failure_code_is_not_collapsed() -> None:
    assert runtime._contract_failure_reason(
        ["agent2_missing_data_reason_missing"]
    ) == "agent2_missing_data_reason_missing"
    assert runtime._contract_failure_reason(
        ["agent2_conflict_reason_missing"]
    ) == "agent2_conflict_reason_missing"
    assert runtime._contract_failure_reason(
        ["agent2_rejected_reason_missing"]
    ) == "agent2_rejected_reason_missing"
    assert runtime._contract_failure_reason(
        ["creativeDraft.directions_min_2"]
    ) == "agent2_draft_contract_invalid"
