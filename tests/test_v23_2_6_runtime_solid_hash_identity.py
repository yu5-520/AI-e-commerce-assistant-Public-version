from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from pathlib import Path

from src.services import agent2_hash_proof_bridge_v22515_service as bridge

ROOT = Path(__file__).resolve().parents[1]
GENERIC_HASH_RUNTIME = ROOT / "src/services/hash_directed_artifact_runtime_v2259_service.py"
AGENT2_RUNTIME = ROOT / "src/services/agent2_runtime_v22515_service.py"
BRIDGE = ROOT / "src/services/agent2_hash_proof_bridge_v22515_service.py"


def _valid_nonready_draft() -> dict:
    target = {"targetType": "product_creative_asset", "targetId": "P10001"}
    return {
        "packageId": "PKG-1",
        "productId": "P10001",
        "storeId": "DY-SH-003",
        "actionFamily": "title_image_test",
        "draftStatus": "draft_missing_data",
        "primaryProblemNode": "点击率下降",
        "primaryAction": "标题主图测试",
        "primaryExecutionTarget": target,
        "primaryOwner": "运营",
        "executionTargets": [target],
        "missingData": ["缺少平台当前主图素材文件"],
        "executionHash": "stable-replay-key-1",
        "outputArtifactRef": "ART-OUTPUT-1",
        "outputContentHash": "sha256:output-1",
    }


def _invalid_nonready_draft() -> dict:
    return {**_valid_nonready_draft(), "missingData": []}


def _canonical_envelope() -> dict:
    payload = {
        "packageId": "PKG-1",
        "productId": "P10001",
        "storeId": "DY-SH-003",
        "dataVersion": "DV-001",
    }
    return {
        "schema": "agent_input.agent2_draft.v1",
        "projectionVersion": "22.5.0",
        "sourceArtifactRefs": ["ART-SOURCE-1"],
        "sourceContentHash": "sha256:source",
        "projectedContentHash": "semantic-projected-hash-1",
        "payload": payload,
        "projectionAudit": {"stage": "agent2_draft"},
        "hardInterface": {"enabled": True, "fallbackAllowed": False},
    }


def _install_runtime(monkeypatch, tmp_path):
    db_path = tmp_path / "runtime.sqlite3"
    artifacts: dict[str, dict] = {"ART-INPUT-1": _canonical_envelope()}
    artifact_hashes = {
        "ART-INPUT-1": "sha256:" + "1" * 64,
        "ART-OUTPUT-1": "sha256:" + "2" * 64,
    }
    receipt_counter = {"value": 0}

    def connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_generic_tables():
        with connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS artifact_execution_index_v2259 (
                    execution_hash TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    item_execution_id TEXT NOT NULL,
                    input_artifact_ref TEXT NOT NULL,
                    input_content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    accepted_output_ref TEXT,
                    accepted_output_hash TEXT,
                    raw_batch_output_ref TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    lease_expires_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def validate_artifact(artifact_ref, expected_type=None):
        del expected_type
        return {
            "ok": artifact_ref in artifacts,
            "status": "valid" if artifact_ref in artifacts else "missing",
            "contentHash": artifact_hashes.get(artifact_ref),
        }

    def resolve_artifact(artifact_ref):
        return artifacts[artifact_ref]

    def inspect_artifact(artifact_ref):
        return {"contentHash": artifact_hashes[artifact_ref]}

    def store_artifact(**kwargs):
        receipt_counter["value"] += 1
        artifact_ref = f"ART-STORED-{receipt_counter['value']}"
        value = kwargs["value"]
        artifacts[artifact_ref] = value
        artifact_hashes[artifact_ref] = "sha256:" + hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "artifactId": artifact_ref,
            "contentHash": artifact_hashes[artifact_ref],
        }

    monkeypatch.setattr(bridge, "connect", connect)
    monkeypatch.setattr(bridge, "ensure_hash_directed_runtime_tables", ensure_generic_tables)
    monkeypatch.setattr(bridge, "validate_artifact", validate_artifact)
    monkeypatch.setattr(bridge, "resolve_artifact", resolve_artifact)
    monkeypatch.setattr(bridge, "inspect_artifact", inspect_artifact)
    monkeypatch.setattr(bridge, "store_artifact", store_artifact)
    bridge.ensure_agent2_runtime_identity_tables()
    return connect, artifacts, artifact_hashes


def _seed_accepted_execution(connect, *, execution_hash="stable-replay-key-1") -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO artifact_execution_index_v2259 (
                execution_hash,stage,item_execution_id,input_artifact_ref,
                input_content_hash,status,accepted_output_ref,accepted_output_hash,
                raw_batch_output_ref,attempt_count,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                execution_hash,
                bridge.AGENT2_HASH_STAGE,
                "EXE-SOURCE",
                "ART-INPUT-1",
                "sha256:" + "1" * 64,
                "accepted",
                "ART-OUTPUT-1",
                "sha256:output-1",
                "ART-RAW-1",
                1,
                "2026-07-29T00:00:00+00:00",
            ),
        )
        conn.commit()


def test_solid_content_hash_ignores_runtime_metadata() -> None:
    first = {
        "draftStatus": "draft_ready",
        "creativeDraft": {"directions": [{"fullTitle": "A"}, {"fullTitle": "B"}]},
        "executionHash": "E1",
        "runtimeExecutionHash": "R1",
        "executionId": "X1",
        "attemptNo": 1,
        "createdAt": "2026-07-29T01:00:00Z",
        "artifactRefs": {"raw": "ART-A"},
    }
    second = {
        **first,
        "executionHash": "E2",
        "runtimeExecutionHash": "R2",
        "executionId": "X2",
        "attemptNo": 2,
        "createdAt": "2026-07-29T02:00:00Z",
        "artifactRefs": {"raw": "ART-B"},
    }
    changed = {
        **second,
        "creativeDraft": {"directions": [{"fullTitle": "A"}, {"fullTitle": "C"}]},
    }

    assert bridge.stable_agent2_content_hash(first) == bridge.stable_agent2_content_hash(second)
    assert bridge.stable_agent2_content_hash(first) != bridge.stable_agent2_content_hash(changed)


def test_same_semantic_input_creates_unique_runtime_transactions(monkeypatch, tmp_path) -> None:
    connect, artifacts, _ = _install_runtime(monkeypatch, tmp_path)
    draft = _valid_nonready_draft()

    first = bridge.record_agent2_runtime_outcome(
        input_ref="ART-INPUT-1",
        draft=draft,
        execution_mode="provider_call",
        status="accepted",
        contract_version="23.2.6",
    )
    second = bridge.record_agent2_runtime_outcome(
        input_ref="ART-INPUT-1",
        draft=draft,
        execution_mode="manual_recovery",
        status="accepted",
        contract_version="23.2.6",
    )

    assert first["semanticInputHash"] == second["semanticInputHash"]
    assert first["semanticInputHash"] == "semantic-projected-hash-1"
    assert first["replayKeyHash"] == second["replayKeyHash"]
    assert first["acceptedContentHash"] == second["acceptedContentHash"]
    assert first["runtimeExecutionHash"] != second["runtimeExecutionHash"]
    assert first["executionId"] != second["executionId"]
    assert first["attemptNo"] == 1
    assert second["attemptNo"] == 2
    assert first["runtimeExecutionReceiptRef"] != second["runtimeExecutionReceiptRef"]
    assert first["runtimeExecutionReceiptRef"] in artifacts
    assert second["runtimeExecutionReceiptRef"] in artifacts

    with connect() as conn:
        rows = conn.execute(
            "SELECT execution_mode,attempt_no FROM agent2_runtime_execution_v2326 ORDER BY attempt_no"
        ).fetchall()
    assert [row["execution_mode"] for row in rows] == ["provider_call", "manual_recovery"]
    assert [row["attempt_no"] for row in rows] == [1, 2]


def test_invalid_historical_output_is_revoked_without_deleting_artifacts(monkeypatch, tmp_path) -> None:
    connect, _, _ = _install_runtime(monkeypatch, tmp_path)
    _seed_accepted_execution(connect)

    result = bridge.finalize_agent2_execution_acceptance(
        _invalid_nonready_draft(),
        contract_version="23.2.6",
    )

    assert result["accepted"] is False
    assert "agent2_missing_data_reason_missing" in result["missing"]
    with connect() as conn:
        row = conn.execute(
            """
            SELECT status,reusable,replay_rejection_reason,accepted_output_ref,
                   accepted_output_hash,raw_batch_output_ref
            FROM artifact_execution_index_v2259
            WHERE execution_hash='stable-replay-key-1'
            """
        ).fetchone()
    assert row["status"] == "failed"
    assert row["reusable"] == 0
    assert "accepted_output_current_contract_invalid" in row["replay_rejection_reason"]
    assert row["accepted_output_ref"] == "ART-OUTPUT-1"
    assert row["accepted_output_hash"] == "sha256:output-1"
    assert row["raw_batch_output_ref"] == "ART-RAW-1"

    revoked = bridge.revoked_agent2_execution_for_input("ART-INPUT-1")
    assert revoked is not None
    assert revoked["execution_hash"] == "stable-replay-key-1"
    assert revoked["accepted_output_ref"] == "ART-OUTPUT-1"
    assert revoked["raw_batch_output_ref"] == "ART-RAW-1"


def test_regeneration_envelope_changes_file_identity_not_semantic_payload(monkeypatch, tmp_path) -> None:
    _, artifacts, artifact_hashes = _install_runtime(monkeypatch, tmp_path)
    canonical = _canonical_envelope()

    first = bridge.build_agent2_regeneration_envelope(
        canonical,
        canonical_input_ref="ART-INPUT-1",
        source_execution_hash="stable-replay-key-1",
    )
    second = bridge.build_agent2_regeneration_envelope(
        canonical,
        canonical_input_ref="ART-INPUT-1",
        source_execution_hash="stable-replay-key-1",
    )

    assert first["runtimeInputArtifactRef"] != second["runtimeInputArtifactRef"]
    assert first["runtimeInputArtifactHash"] != second["runtimeInputArtifactHash"]
    assert first["semanticInputHash"] == second["semanticInputHash"]
    assert first["semanticInputHash"] == canonical["projectedContentHash"]
    assert first["envelope"]["payload"] == canonical["payload"]
    assert second["envelope"]["payload"] == canonical["payload"]
    assert first["runtimeAttemptId"] != second["runtimeAttemptId"]
    assert artifacts[first["runtimeInputArtifactRef"]]["projectionAudit"]["runtimeExecution"]["createdAt"]
    assert artifact_hashes[first["runtimeInputArtifactRef"]] == first["runtimeInputArtifactHash"]


def test_valid_output_is_promoted_to_solid_content_hash(monkeypatch, tmp_path) -> None:
    connect, _, _ = _install_runtime(monkeypatch, tmp_path)
    _seed_accepted_execution(connect)
    draft = _valid_nonready_draft()

    result = bridge.finalize_agent2_execution_acceptance(
        draft,
        contract_version="23.2.6",
    )

    assert result["accepted"] is True
    assert result["acceptedContentHash"] == bridge.stable_agent2_content_hash(draft)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT status,reusable,replay_rejection_reason,accepted_content_hash,
                   accepted_contract_version
            FROM artifact_execution_index_v2259
            WHERE execution_hash='stable-replay-key-1'
            """
        ).fetchone()
    assert row["status"] == "accepted"
    assert row["reusable"] == 1
    assert row["replay_rejection_reason"] is None
    assert row["accepted_content_hash"] == result["acceptedContentHash"]
    assert row["accepted_contract_version"] == "23.2.6"


def test_agent2_scope_keeps_generic_hash_runtime_stable_and_seals_one_regeneration() -> None:
    for path in (GENERIC_HASH_RUNTIME, AGENT2_RUNTIME, BRIDGE):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    generic = GENERIC_HASH_RUNTIME.read_text(encoding="utf-8")
    assert "RUNTIME_SOLID_HASH_IDENTITY_VERSION" not in generic
    assert "provider_regeneration_after_invalid_replay" not in generic

    runtime = AGENT2_RUNTIME.read_text(encoding="utf-8")
    assert runtime.count("provider_regeneration_after_invalid_replay") >= 3
    assert "max_items_per_call=1" in runtime
    assert "provider_regeneration_count += 1" in runtime
    assert "revoke_agent2_execution(source_execution_hash, direct_missing)" in runtime
    assert "revoked_agent2_execution_for_input(canonical_input_ref)" in runtime
    assert "preflight_regeneration_by_package" in runtime
    assert "build_agent2_regeneration_envelope" in runtime
    assert "failure_class='agent2_contract'" in runtime

    source = BRIDGE.read_text(encoding="utf-8")
    for marker in (
        "agent2_runtime_execution_v2326",
        "stable_agent2_content_hash",
        "build_agent2_regeneration_envelope",
        "revoked_agent2_execution_for_input",
        "runtimeExecution",
        "replay_rejection_reason",
        "accepted_content_hash",
        "runtimeExecutionHash",
        "sourceExecutionHash",
        "attemptNo",
    ):
        assert marker in source
