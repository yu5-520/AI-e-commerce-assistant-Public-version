from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.sqlite_data_identity import database_identity


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO products(name) VALUES ('demo')")
        conn.commit()


def test_sqlite_data_identity_is_stable_for_same_state(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _create_db(db)
    first = database_identity(db, include_content_hash=True)
    second = database_identity(db, include_content_hash=True)
    assert first["verified"] is True
    assert first["quickCheck"] == "ok"
    assert first["schemaHash"] == second["schemaHash"]
    assert first["stateHash"] == second["stateHash"]
    assert first["contentHash"] == second["contentHash"]
    assert first["tableCount"] == 1


def test_sqlite_data_identity_detects_schema_change(tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _create_db(db)
    before = database_identity(db)
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE products ADD COLUMN sku TEXT")
        conn.commit()
    after = database_identity(db)
    assert before["schemaHash"] != after["schemaHash"]
    assert before["stateHash"] != after["stateHash"]


def test_data_lineage_service_matches_deployment_schema(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _create_db(db)
    live = database_identity(db)
    lineage = tmp_path / "release-data-lineage.json"
    lineage.write_text(
        json.dumps(
            {
                "schema": "release.data-lineage.v1",
                "sourceCommit": "a" * 40,
                "releaseHash": "sha256:" + "b" * 64,
                "databasePath": str(db),
                "schemaHash": live["schemaHash"],
                "deploymentStateHash": live["stateHash"],
                "backupPath": None,
                "backupContentHash": None,
                "quickCheck": "ok",
            }
        ),
        encoding="utf-8",
    )

    import src.services.data_identity_service as service

    monkeypatch.setattr(service, "DB_PATH", str(db))
    monkeypatch.setattr(service, "LINEAGE_PATH", lineage)
    result = service.data_identity()
    assert result["verified"] is True
    assert result["schemaMatch"] is True
    assert result["releaseHash"] == "sha256:" + "b" * 64


def test_data_lineage_service_rejects_schema_drift(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "runtime.sqlite3"
    _create_db(db)
    lineage = tmp_path / "release-data-lineage.json"
    lineage.write_text(
        json.dumps(
            {
                "schema": "release.data-lineage.v1",
                "sourceCommit": "a" * 40,
                "releaseHash": "sha256:" + "b" * 64,
                "schemaHash": "sha256:" + "c" * 64,
            }
        ),
        encoding="utf-8",
    )

    import src.services.data_identity_service as service

    monkeypatch.setattr(service, "DB_PATH", str(db))
    monkeypatch.setattr(service, "LINEAGE_PATH", lineage)
    result = service.data_identity()
    assert result["verified"] is False
    assert result["schemaMatch"] is False
    assert "data_schema_hash_mismatch" in result["errors"]
