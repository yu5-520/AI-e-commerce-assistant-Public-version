from __future__ import annotations

import json
from pathlib import Path

import pytest


def _isolate_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.repositories import sqlite_repository
    from src.services import artifact_storage_service

    monkeypatch.setattr(sqlite_repository, "DB_PATH", tmp_path / "runtime.sqlite3")
    monkeypatch.setattr(sqlite_repository, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sqlite_repository, "_WAL_INITIALIZED", False)
    monkeypatch.setattr(artifact_storage_service, "ARTIFACT_ROOT", tmp_path / "artifacts")


def test_non_agent_stage_artifact_overrides_stale_database_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)

    from src.repositories.sqlite_repository import connect, dumps
    from src.services.pipeline_artifact_contract_service import resolve_pipeline_row
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item

    envelope = build_item_envelope(
        data_version="DV-1",
        product_id="P-1",
        store_id="S-1",
        stage="agent1_completed",
    )
    upsert_pipeline_item(
        envelope,
        stage="agent1_completed",
        status="ready",
        payload={"authoritativeValue": "artifact"},
    )
    with connect() as conn:
        row = conn.execute("SELECT * FROM pipeline_items LIMIT 1").fetchone()
        conn.execute(
            "UPDATE pipeline_items SET payload=? WHERE item_id=?",
            (dumps({"payload": {"authoritativeValue": "stale"}}), row["item_id"]),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pipeline_items LIMIT 1").fetchone()

    resolved = resolve_pipeline_row(row, expected_stage="agent1_completed")
    assert resolved.source == "artifact_ref_only"
    assert resolved.payload["authoritativeValue"] == "artifact"


def test_agent_stages_require_projection_refs_and_never_use_full_upstream_refs() -> None:
    from src.services.pipeline_artifact_contract_service import (
        PipelineArtifactContractError,
        input_artifact_id,
        pipeline_input_ref,
    )

    agent1_row = {
        "item_id": "PI-A1",
        "current_stage": "agent1_pending",
        "payload_artifact_ref": "ART-SIGNAL",
        "artifact_refs_json": json.dumps(
            {"signalRef": "ART-SIGNAL", "currentStageRef": "ART-SIGNAL"}
        ),
    }
    agent2_row = {
        "item_id": "PI-A2",
        "current_stage": "action_pack_ready",
        "payload_artifact_ref": "ART-CAPABILITY",
        "artifact_refs_json": json.dumps(
            {
                "capabilityRef": "ART-CAPABILITY",
                "currentStageRef": "ART-CAPABILITY",
            }
        ),
    }
    assert input_artifact_id(agent1_row) is None
    assert input_artifact_id(agent2_row) is None
    with pytest.raises(PipelineArtifactContractError) as a1:
        pipeline_input_ref(agent1_row)
    with pytest.raises(PipelineArtifactContractError) as a2:
        pipeline_input_ref(agent2_row)
    assert a1.value.code == "hard_agent_input_ref_missing"
    assert a2.value.code == "hard_agent_input_ref_missing"

    agent1_row["artifact_refs_json"] = json.dumps(
        {
            "signalRef": "ART-SIGNAL",
            "agent1InputRef": "ART-A1-INPUT",
            "currentStageRef": "ART-A1-INPUT",
        }
    )
    agent2_row["artifact_refs_json"] = json.dumps(
        {
            "capabilityRef": "ART-CAPABILITY",
            "agent2InputRef": "ART-A2-INPUT",
            "currentStageRef": "ART-A2-INPUT",
        }
    )
    assert pipeline_input_ref(agent1_row) == "ART-A1-INPUT"
    assert pipeline_input_ref(agent2_row) == "ART-A2-INPUT"


def test_invalid_existing_ref_never_falls_back_to_stale_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)

    from src.repositories.sqlite_repository import connect, dumps
    from src.services.pipeline_artifact_contract_service import (
        PipelineArtifactContractError,
        resolve_pipeline_row,
    )
    from src.services.pipeline_item_service import ensure_pipeline_item_tables

    ensure_pipeline_item_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_items(
                item_id,data_version,product_id,store_id,current_stage,status,
                payload,artifact_refs_json,payload_artifact_ref,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "PI-BROKEN",
                "DV-1",
                "P-1",
                "S-1",
                "agent2_completed",
                "ready",
                dumps({"payload": {"agent2ActionPlan": {"actionPlanStatus": "ready"}}}),
                dumps({"agent2Ref": "ART-MISSING", "currentStageRef": "ART-MISSING"}),
                "ART-MISSING",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pipeline_items WHERE item_id='PI-BROKEN'").fetchone()

    with pytest.raises(PipelineArtifactContractError) as exc_info:
        resolve_pipeline_row(row, expected_stage="agent2_completed")
    assert exc_info.value.code == "artifact_not_found"


def test_row_without_ref_is_blocked_until_one_time_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)

    from src.repositories.sqlite_repository import connect, dumps
    from src.services.pipeline_artifact_contract_service import (
        PipelineArtifactContractError,
        resolve_pipeline_row,
    )
    from src.services.pipeline_item_service import ensure_pipeline_item_tables
    from src.services.pipeline_payload_retirement_service import migrate_pipeline_payloads_to_artifacts

    ensure_pipeline_item_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_items(
                item_id,data_version,product_id,current_stage,status,payload,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                "PI-LEGACY",
                "DV-OLD",
                "P-OLD",
                "agent1_completed",
                "ready",
                dumps({"payload": {"legacyValue": "migrate_once"}}),
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM pipeline_items WHERE item_id='PI-LEGACY'").fetchone()

    with pytest.raises(PipelineArtifactContractError) as exc_info:
        resolve_pipeline_row(row, expected_stage="agent1_completed", allow_legacy_payload=True)
    assert exc_info.value.code == "legacy_payload_runtime_retired"

    migration = migrate_pipeline_payloads_to_artifacts(fail_on_unmigrated=True)
    assert migration["status"]["sealed"] is True
    with connect() as conn:
        migrated = conn.execute("SELECT * FROM pipeline_items WHERE item_id='PI-LEGACY'").fetchone()
    resolved = resolve_pipeline_row(migrated, expected_stage="agent1_completed")
    assert resolved.payload["legacyValue"] == "migrate_once"


def test_reference_only_trigger_strips_new_pipeline_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)

    from src.repositories.sqlite_repository import connect
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item
    from src.services.pipeline_payload_retirement_service import (
        migrate_pipeline_payloads_to_artifacts,
        payload_retirement_status,
    )

    migrate_pipeline_payloads_to_artifacts(fail_on_unmigrated=True)
    envelope = build_item_envelope(
        data_version="DV-NEW",
        product_id="P-NEW",
        store_id="S-NEW",
        stage="agent1_completed",
    )
    upsert_pipeline_item(
        envelope,
        stage="agent1_completed",
        status="ready",
        payload={"decisionType": "act", "largeSemanticPackage": "do_not_duplicate"},
    )
    with connect() as conn:
        row = conn.execute("SELECT * FROM pipeline_items LIMIT 1").fetchone()
    assert row["payload"] is None
    assert str(row["payload_artifact_ref"]).startswith("ART-")
    status = payload_retirement_status()
    assert status["semanticPayloadRowCount"] == 0
    assert status["writeMode"] == "artifact_ref_only"
    assert status["sealed"] is True
