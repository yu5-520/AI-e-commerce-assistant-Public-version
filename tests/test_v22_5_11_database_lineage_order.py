from __future__ import annotations

from pathlib import Path

import pytest

from src import runtime_version as rv
from src.services import runtime_database_prepare_v22511_service as prepare

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_schema_preparation_runs_all_release_owned_schema_owners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    owners = [
        "ensure_artifact_storage",
        "init_db",
        "ensure_artifact_tables",
        "ensure_pipeline_item_tables",
        "ensure_action_authority_tables",
        "ensure_llm_cache_table",
        "ensure_agent1_runtime_columns",
        "ensure_agent2_runtime_columns",
        "ensure_agent3_runtime_columns",
        "ensure_hash_directed_runtime_tables",
        "ensure_frontend_view_artifact_tables",
    ]
    for name in owners:
        monkeypatch.setattr(prepare, name, lambda name=name: called.append(name))
    monkeypatch.setattr(
        prepare,
        "backfill_task_detail_snapshots",
        lambda *, limit: called.append(f"task_detail:{limit}"),
    )
    identities = iter(
        [
            {"verified": True, "schemaHash": "old", "quickCheck": "ok"},
            {
                "verified": True,
                "schemaHash": "prepared",
                "stateHash": "state",
                "databasePath": "/tmp/db",
                "quickCheck": "ok",
            },
            {
                "verified": True,
                "schemaHash": "prepared",
                "stateHash": "state",
                "databasePath": "/tmp/db",
                "quickCheck": "ok",
            },
        ]
    )
    monkeypatch.setattr(prepare, "_identity", lambda: next(identities))

    result = prepare.prepare_runtime_database_schema(verify_idempotent=True)

    expected_once = owners + ["task_detail:0"]
    assert called == expected_once + expected_once
    assert result["preparedSchemaHash"] == "prepared"
    assert result["idempotenceChecked"] is True
    assert result["idempotent"] is True
    assert result["businessMigrationExecuted"] is False
    assert result["leaseRecoveryExecuted"] is False


def test_schema_preparation_fails_when_second_pass_changes_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(prepare, "_ensure_release_owned_schema_once", lambda: None)
    identities = iter(
        [
            {"verified": True, "schemaHash": "old", "quickCheck": "ok"},
            {"verified": True, "schemaHash": "first", "quickCheck": "ok"},
            {"verified": True, "schemaHash": "second", "quickCheck": "ok"},
        ]
    )
    monkeypatch.setattr(prepare, "_identity", lambda: next(identities))
    with pytest.raises(RuntimeError, match="runtime_database_schema_not_idempotent"):
        prepare.prepare_runtime_database_schema(verify_idempotent=True)


def test_deployment_prepares_schema_before_lineage_and_before_service_start() -> None:
    source = _read("scripts/deploy_release.sh")
    attach = source.index('log "9. Attach shared state')
    prepare_step = source.index('log "10. Prepare final SQLite schema')
    lineage = source.index('"schemaPreparedBeforeLineage":True')
    switch = source.index('log "11. Bind systemd')
    post_start = source.index('log "12. Verify API')
    assert attach < prepare_step < lineage < switch < post_start
    assert "--verify-idempotent" in source
    assert 'assert p.get("idempotent") is True' in source
    assert 'assert live.get("schemaHash")==os.environ["PREPARED_SCHEMA_HASH"]' in source
    assert '(v.get("database") or {}).get("schemaHash")==os.environ["PREPARED_SCHEMA_HASH"]' in source
    assert '(v.get("lineage") or {}).get("schemaPreparedBeforeLineage") is True' in source


def test_failed_candidate_restores_database_lineage_and_service_health() -> None:
    source = _read("scripts/deploy_release.sh")
    assert "restore_database_and_lineage" in source
    assert "ROLLBACK_DATABASE_RESTORED" in source
    assert "ROLLBACK_LINEAGE_RESTORED" in source
    assert "ROLLBACK_SERVICE_RESTORED" in source
    assert "PRAGMA quick_check" in source
    assert 'os.replace(temporary, target)' in source
    assert 'curl -fsS --connect-timeout 3 --max-time 5 "http://127.0.0.1:${PORT}/api/health"' in source
    assert 'systemctl start "$SERVICE" || true' not in source


def test_deployment_contract_does_not_assume_port_8000() -> None:
    source = _read("scripts/deploy_release.sh")
    assert 'PORT="$(resolve_ai_runtime_port "$ROOT_DIR" "$SERVICE")"' in source
    assert "127.0.0.1:8000" not in source
    assert '127.0.0.1:${PORT}' in source


def test_runtime_version_exposes_data_lineage_order_contract() -> None:
    versions = rv.runtime_versions()
    assert rv.DEPLOYMENT_DATABASE_LINEAGE_VERSION == "22.5.11"
    assert rv.RUNTIME_DATABASE_SCHEMA_PREP_VERSION == "22.5.11"
    assert rv.ROLLBACK_DATABASE_RESTORE_VERSION == "22.5.11"
    assert versions["deploymentDatabaseLineage"] == "22.5.11"
    assert versions["runtimeDatabaseSchemaPreparation"] == "22.5.11"
    contracts = versions["deploymentDataContracts"]
    assert contracts["schemaPreparedBeforeLineage"] is True
    assert contracts["schemaPreparationIdempotenceRequired"] is True
    assert contracts["postStartSchemaHashMustMatchPreparedHash"] is True
    assert contracts["rollbackDatabaseBackupRestoreRequired"] is True
    assert contracts["rollbackLineageRestoreRequired"] is True
    assert contracts["rollbackServiceHealthVerificationRequired"] is True


def test_documentation_records_the_failure_and_fixed_order() -> None:
    document = _read("docs/V22.5.11_DATABASE_LINEAGE_DEPLOYMENT.md")
    note = _read("release/notes/V22.5.11.md")
    for value in (
        "schemaPreparedBeforeLineage",
        "preparedSchemaHash",
        "ROLLBACK_DATABASE_RESTORED",
        "ROLLBACK_LINEAGE_RESTORED",
        "ROLLBACK_SERVICE_RESTORED",
    ):
        assert value in document
    assert "pre-start schemaHash = A" in document
    assert "live schemaHash = B" in document
    assert "Root Verifier" in note
    assert "Root Verifier" in document
    assert "one Worker" in document
