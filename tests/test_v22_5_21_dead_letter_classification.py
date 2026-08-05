from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.services import agent2_runtime_v22521_service as runtime

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "services" / "agent2_runtime_v22521_service.py"


def test_source_parses() -> None:
    ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))


def test_row_failure_is_merged_even_when_payload_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime.legacy_runtime.legacy_v22514,
        "payload_from_row",
        lambda _row: {"packageId": "PKG-1", "stalePayload": True},
    )
    item = {
        "item_id": "PI-1",
        "package_id": "PKG-1",
        "data_version": "DV-1",
        "current_stage": "agent2_dead_letter",
        "status": "failed",
        "retry_count": 3,
        "error_reason": "agent2_draft_returned_no_plan",
        "last_error_code": None,
        "failure_code": None,
        "failure_class": None,
        "last_error_artifact_ref": None,
    }

    evidence = runtime.dead_letter_classification_evidence_v22521(item)
    classified = runtime.classify_agent2_dead_letter_v22521(item)

    assert evidence["payload"]["stalePayload"] is True
    assert evidence["rowFailure"]["errorReason"] == "agent2_draft_returned_no_plan"
    assert evidence["rowFailureMergedUnconditionally"] is True
    assert classified["classification"] == "true_missing_candidate"


def test_hash_marker_in_row_is_not_suppressed_by_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime.legacy_runtime.legacy_v22514,
        "payload_from_row",
        lambda _row: {"packageId": "PKG-2", "result": "old readable payload"},
    )
    item = {
        "item_id": "PI-2",
        "package_id": "PKG-2",
        "data_version": "DV-1",
        "current_stage": "agent2_dead_letter",
        "status": "failed",
        "retry_count": 3,
        "error_reason": "agent2_draft_item_provenance_missing",
        "last_error_code": None,
        "failure_code": None,
        "failure_class": None,
        "last_error_artifact_ref": None,
    }

    classified = runtime.classify_agent2_dead_letter_v22521(item)

    assert classified["hashProofCandidate"] is True
    assert classified["classification"] == "hash_proof_candidate"


def test_ambiguous_evidence_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime.legacy_runtime.legacy_v22514,
        "payload_from_row",
        lambda _row: {"reason": "agent2_draft_returned_no_plan"},
    )
    item = {
        "item_id": "PI-3",
        "package_id": "PKG-3",
        "data_version": "DV-1",
        "current_stage": "agent2_dead_letter",
        "status": "failed",
        "retry_count": 3,
        "error_reason": "agent2_draft_item_provenance_missing",
        "last_error_code": None,
        "failure_code": None,
        "failure_class": None,
        "last_error_artifact_ref": None,
    }

    classified = runtime.classify_agent2_dead_letter_v22521(item)

    assert classified["classification"] == "ambiguous_hash_and_true_missing"
    assert classified["fallbackAllowed"] is False


def test_repair_contract_never_reruns_upstream() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    for marker in (
        'AGENT2_RUNTIME_VERSION = "22.5.21"',
        '"agent1Rerun": False',
        '"actionPackRerun": False',
        '"agent2InputProjectionRerun": False',
        '"rowFailureMergedUnconditionally": True',
        '"payloadReadabilityDoesNotSuppressRowFailure": True',
        "repair_agent2_dead_letters_v22521",
    ):
        assert marker in source
    assert "run_agent1" not in source
    assert "ensure_agent2_draft_input_ref" not in source
