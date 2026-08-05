from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import sys
import time
from collections import namedtuple
from pathlib import Path

import pytest


def _load_backup_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "sqlite_backup_rotate.py"
    spec = importlib.util.spec_from_file_location("sqlite_backup_rotate_v2262", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE facts(id INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO facts(value) VALUES (?)",
            [(f"value-{index}",) for index in range(500)],
        )
        conn.commit()


def test_backup_rotation_removes_old_and_incomplete_copies(tmp_path: Path) -> None:
    module = _load_backup_module()
    source = tmp_path / "product_workbench.sqlite3"
    backup_dir = tmp_path / "deployment_backups"
    backup_dir.mkdir()
    _make_db(source)

    old_first = backup_dir / "product_workbench-pre-v22.2.4-first.sqlite3"
    old_latest = backup_dir / "product_workbench-pre-v22.2.6-latest.sqlite3"
    shutil.copy2(source, old_first)
    shutil.copy2(source, old_latest)
    now = time.time()
    os.utime(old_first, (now - 20, now - 20))
    os.utime(old_latest, (now - 10, now - 10))

    incomplete = backup_dir / "product_workbench-pre-v22.2.6-failed.sqlite3"
    incomplete.write_bytes(b"incomplete")
    journal = backup_dir / "product_workbench-pre-v22.2.6-failed.sqlite3-journal"
    journal.write_bytes(b"journal")

    result = module.create_rotating_backup(
        source=source,
        backup_dir=backup_dir,
        prefix="product_workbench-pre-",
        filename="product_workbench-pre-v22.2.6-new.sqlite3",
        keep=1,
        reserve_ratio=0,
        min_reserve_bytes=0,
    )

    backups = list(backup_dir.glob("*.sqlite3"))
    assert backups == [backup_dir / "product_workbench-pre-v22.2.6-new.sqlite3"]
    assert not incomplete.exists()
    assert not journal.exists()
    assert result["status"] == "completed"
    assert result["version"] == "22.2.6.2"
    assert result["quickCheck"] == "ok"
    assert result["retentionKeepCount"] == 1
    assert result["removedBytes"] > 0

    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 500
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_valid_smaller_historical_backup_is_not_treated_as_truncated(
    tmp_path: Path,
) -> None:
    module = _load_backup_module()
    source = tmp_path / "product_workbench.sqlite3"
    backup_dir = tmp_path / "deployment_backups"
    backup_dir.mkdir()
    _make_db(source)

    historical = backup_dir / "product_workbench-pre-v22.2.4-valid-smaller.sqlite3"
    shutil.copy2(source, historical)
    with sqlite3.connect(source) as conn:
        conn.executemany(
            "INSERT INTO facts(value) VALUES (?)",
            [("x" * 4096,) for _ in range(1000)],
        )
        conn.commit()
    assert historical.stat().st_size < source.stat().st_size

    result = module.create_rotating_backup(
        source=source,
        backup_dir=backup_dir,
        prefix="product_workbench-pre-",
        filename="product_workbench-pre-v22.2.6-new.sqlite3",
        keep=2,
        reserve_ratio=0,
        min_reserve_bytes=0,
    )

    assert historical.exists()
    assert (backup_dir / "product_workbench-pre-v22.2.6-new.sqlite3").exists()
    assert not any(
        item["path"] == str(historical)
        and item["reason"] == "invalid_or_truncated_backup"
        for item in result["removedFiles"]
    )


def test_backup_preflight_fails_before_partial_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_backup_module()
    source = tmp_path / "product_workbench.sqlite3"
    backup_dir = tmp_path / "deployment_backups"
    _make_db(source)

    DiskUsage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(
        module.shutil,
        "disk_usage",
        lambda _: DiskUsage(total=100, used=100, free=0),
    )

    with pytest.raises(module.BackupError, match="Insufficient disk space"):
        module.create_rotating_backup(
            source=source,
            backup_dir=backup_dir,
            prefix="product_workbench-pre-",
            filename="product_workbench-pre-v22.2.6-new.sqlite3",
            keep=1,
            reserve_ratio=0,
            min_reserve_bytes=0,
        )

    assert not list(backup_dir.glob("*.partial"))
    assert not list(backup_dir.glob("*.sqlite3"))
