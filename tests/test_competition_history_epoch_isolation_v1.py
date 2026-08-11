from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from src.services import canonical_product_trend_v2_service as trend
from src.services import competition_history_epoch_service as epoch


@contextmanager
def _connection_scope(conn: sqlite3.Connection):
    yield conn


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE runtime_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE canonical_product_snapshot_sets_v1 (
            snapshot_id TEXT PRIMARY KEY,
            data_version TEXT,
            set_snapshot_hash TEXT NOT NULL,
            product_count INTEGER DEFAULT 0,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _insert_snapshot(conn: sqlite3.Connection, snapshot_id: str, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO canonical_product_snapshot_sets_v1(
            snapshot_id,data_version,set_snapshot_hash,product_count,payload,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            snapshot_id,
            f"DV-{snapshot_id}",
            f"sha256:{snapshot_id}",
            1,
            '{"products":[]}',
            created_at,
            created_at,
        ),
    )
    conn.commit()


def test_legacy_database_bootstraps_from_latest_snapshot_and_then_accumulates(monkeypatch):
    conn = _memory_db()
    _insert_snapshot(conn, "old-1", "2026-08-11T10:00:00")
    _insert_snapshot(conn, "latest", "2026-08-11T11:00:00")
    monkeypatch.setattr(epoch, "connect", lambda: _connection_scope(conn))

    first = epoch.current_competition_history_epoch()
    assert first["bootstrapMode"] == "legacy_latest_snapshot_fail_closed"
    assert first["startedAt"] == "2026-08-11T11:00:00"
    assert first["bootstrapSnapshotId"] == "latest"
    assert first["crossEpochHistoryAllowed"] is False

    _insert_snapshot(conn, "next", "2026-08-11T12:00:00")
    second = epoch.current_competition_history_epoch()
    assert second["epochId"] == first["epochId"]
    assert second["startedAt"] == first["startedAt"]


def test_existing_system_reset_rotates_epoch_without_deleting_archive(monkeypatch):
    conn = _memory_db()
    _insert_snapshot(conn, "archived", "2026-08-11T10:00:00")
    monkeypatch.setattr(epoch, "connect", lambda: _connection_scope(conn))

    before = epoch.current_competition_history_epoch()
    conn.execute(
        "INSERT OR REPLACE INTO runtime_meta(key,value,updated_at) VALUES (?,?,?)",
        ("latest_demo_reset_scope", "demo", "2026-08-11 13:00:00"),
    )
    conn.commit()

    after = epoch.current_competition_history_epoch()
    assert after["epochId"] != before["epochId"]
    assert after["startedAt"] == "2026-08-11 13:00:00"
    assert after["bootstrapMode"] == "system_demo_reset_boundary"
    assert after["archivePreserved"] is True
    assert conn.execute("SELECT COUNT(*) AS c FROM canonical_product_snapshot_sets_v1").fetchone()["c"] == 1


def test_history_metadata_reads_only_current_epoch(monkeypatch):
    conn = _memory_db()
    _insert_snapshot(conn, "old", "2026-08-11T09:00:00")
    _insert_snapshot(conn, "current-1", "2026-08-11T11:00:00")
    _insert_snapshot(conn, "current-2", "2026-08-11T12:00:00")
    monkeypatch.setattr(trend, "connect", lambda: _connection_scope(conn))
    monkeypatch.setattr(trend, "ensure_snapshot_tables", lambda: None)

    rows = trend._history_metadata("2026-08-11T11:00:00", limit=10)
    assert [row["snapshot_id"] for row in rows] == ["current-2", "current-1"]
    assert all(row["snapshot_id"] != "old" for row in rows)


def test_product_trend_exposes_current_epoch_provenance(monkeypatch):
    trend._CACHE.clear()
    monkeypatch.setattr(
        trend,
        "current_competition_history_epoch",
        lambda: {
            "epochId": "HIST-EPOCH-test",
            "startedAt": "2026-08-11T11:00:00",
            "bootstrapMode": "legacy_latest_snapshot_fail_closed",
            "archivePreserved": True,
        },
    )
    monkeypatch.setattr(trend, "_history_metadata", lambda started_at, limit=trend.MAX_SNAPSHOT_SCAN: [])
    monkeypatch.setattr(
        trend,
        "build_product_trend_projection",
        lambda snapshots, product_id, store_id=None: {"effectiveSnapshotCount": len(snapshots)},
    )

    result = trend.read_canonical_product_trend("P10002", store_id="JD-SH-002")
    assert result["effectiveSnapshotCount"] == 0
    assert result["historyEpochId"] == "HIST-EPOCH-test"
    assert result["historyScope"] == "current_competition_runtime_epoch"
    assert result["crossEpochHistoryAllowed"] is False
    assert result["canonicalArchivePreserved"] is True
