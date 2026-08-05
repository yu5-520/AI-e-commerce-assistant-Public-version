#!/usr/bin/env python3
"""Create one validated SQLite backup and rotate historical deployment copies.

The live database is never modified. Existing complete backups are pruned before
copying so low-capacity ECS hosts do not accumulate multi-gigabyte snapshots.
A new backup is first written to ``.partial``, validated, then atomically renamed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

DEFAULT_KEEP = 1
DEFAULT_RESERVE_RATIO = 0.10
DEFAULT_MIN_RESERVE_BYTES = 256 * 1024 * 1024
MIN_COMPLETE_RATIO = 0.90


class BackupError(RuntimeError):
    """Raised when a safe rotating backup cannot be created."""


@dataclass(frozen=True)
class RemovedFile:
    path: str
    sizeBytes: int
    reason: str


def _remove(path: Path, reason: str, removed: list[RemovedFile]) -> None:
    if not path.exists():
        return
    size = path.stat().st_size
    path.unlink()
    removed.append(RemovedFile(str(path), size, reason))


def _backup_candidates(backup_dir: Path, prefix: str) -> list[Path]:
    return sorted(
        (
            path
            for path in backup_dir.glob(f"{prefix}*.sqlite3")
            if path.is_file()
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def _minimum_complete_size(source_size: int) -> int:
    return max(4096, int(source_size * MIN_COMPLETE_RATIO))


def _sqlite_file_complete(path: Path) -> bool:
    """Check the file contains all pages declared in its own SQLite header.

    This deliberately does not compare an old backup with the current live DB:
    a valid historical rollback copy may be smaller because the live DB grew.
    """
    try:
        size = path.stat().st_size
        if size < 4096:
            return False
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            page_count_row = connection.execute("PRAGMA page_count").fetchone()
            page_size_row = connection.execute("PRAGMA page_size").fetchone()
        finally:
            connection.close()
        page_count = int(page_count_row[0] if page_count_row else 0)
        page_size = int(page_size_row[0] if page_size_row else 0)
        declared_size = page_count * page_size
        return page_count > 0 and page_size > 0 and size >= declared_size
    except Exception:
        return False


def _cleanup_incomplete(
    backup_dir: Path,
    prefix: str,
    removed: list[RemovedFile],
) -> None:
    for pattern in (
        f"{prefix}*.partial",
        f"{prefix}*.sqlite3-journal",
        f"{prefix}*.sqlite3-wal",
        f"{prefix}*.sqlite3-shm",
    ):
        for path in backup_dir.glob(pattern):
            if path.is_file():
                _remove(path, "stale_incomplete_sidecar", removed)

    for path in _backup_candidates(backup_dir, prefix):
        if not _sqlite_file_complete(path):
            _remove(path, "invalid_or_truncated_backup", removed)


def _prune_complete(
    backup_dir: Path,
    prefix: str,
    keep: int,
    removed: list[RemovedFile],
    *,
    reason: str,
) -> None:
    candidates = _backup_candidates(backup_dir, prefix)
    for path in candidates[max(0, keep) :]:
        _remove(path, reason, removed)


def _quick_check(path: Path) -> str:
    connection = sqlite3.connect(str(path), timeout=30)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0] if row else "missing_result")
    finally:
        connection.close()


def _copy_sqlite(source: Path, partial: Path) -> None:
    source_connection = sqlite3.connect(str(source), timeout=30)
    target_connection = sqlite3.connect(str(partial), timeout=30)
    try:
        source_connection.backup(target_connection, pages=4096, sleep=0.05)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()


def _removed_payload(files: Iterable[RemovedFile]) -> list[dict[str, object]]:
    return [
        {"path": item.path, "sizeBytes": item.sizeBytes, "reason": item.reason}
        for item in files
    ]


def create_rotating_backup(
    *,
    source: Path,
    backup_dir: Path,
    prefix: str,
    filename: str,
    keep: int = DEFAULT_KEEP,
    reserve_ratio: float = DEFAULT_RESERVE_RATIO,
    min_reserve_bytes: int = DEFAULT_MIN_RESERVE_BYTES,
) -> dict[str, object]:
    source = source.resolve()
    backup_dir = backup_dir.resolve()
    if not source.is_file():
        raise BackupError(f"Live SQLite database not found: {source}")
    if not filename.endswith(".sqlite3") or Path(filename).name != filename:
        raise BackupError("Backup filename must be a plain .sqlite3 file name")
    if keep < 1:
        raise BackupError("Backup retention must keep at least one validated copy")
    if reserve_ratio < 0:
        raise BackupError("reserve_ratio cannot be negative")
    if min_reserve_bytes < 0:
        raise BackupError("min_reserve_bytes cannot be negative")

    backup_dir.mkdir(parents=True, exist_ok=True)
    source_size = source.stat().st_size
    if source_size <= 0:
        raise BackupError(f"Live SQLite database is empty: {source}")

    removed: list[RemovedFile] = []
    _cleanup_incomplete(backup_dir, prefix, removed)
    # Preserve the newest validated rollback point while deleting older copies
    # before the disk-space check.
    _prune_complete(
        backup_dir,
        prefix,
        keep,
        removed,
        reason="pre_backup_retention_prune",
    )

    reserve_bytes = max(min_reserve_bytes, int(source_size * reserve_ratio))
    required_free_bytes = source_size + reserve_bytes
    disk_before = shutil.disk_usage(backup_dir)
    if disk_before.free < required_free_bytes:
        raise BackupError(
            "Insufficient disk space for SQLite backup after rotation: "
            f"free={disk_before.free}, required={required_free_bytes}, "
            f"source={source_size}, reserve={reserve_bytes}, backupDir={backup_dir}"
        )

    final_path = backup_dir / filename
    partial_path = backup_dir / f"{filename}.partial"
    _remove(partial_path, "preexisting_partial_for_same_backup", removed)
    if final_path.exists():
        raise BackupError(f"Backup already exists: {final_path}")

    try:
        _copy_sqlite(source, partial_path)
        partial_size = partial_path.stat().st_size
        if partial_size < _minimum_complete_size(source_size):
            raise BackupError(
                f"SQLite backup is undersized: source={source_size}, backup={partial_size}"
            )
        if not _sqlite_file_complete(partial_path):
            raise BackupError("SQLite backup file is truncated or has an invalid page map")
        quick_check = _quick_check(partial_path)
        if quick_check.lower() != "ok":
            raise BackupError(f"SQLite backup quick_check failed: {quick_check}")
        os.replace(partial_path, final_path)
    except Exception:
        if partial_path.exists():
            _remove(partial_path, "failed_backup_partial", removed)
        raise

    # The newly validated backup is now the rollback point. Retain only the
    # configured number of complete deployment snapshots, including this one.
    _prune_complete(
        backup_dir,
        prefix,
        keep,
        removed,
        reason="post_backup_retention_prune",
    )
    retained = _backup_candidates(backup_dir, prefix)
    disk_after = shutil.disk_usage(backup_dir)
    return {
        "status": "completed",
        "version": "22.2.6.2",
        "source": str(source),
        "sourceSizeBytes": source_size,
        "backupPath": str(final_path),
        "backupSizeBytes": final_path.stat().st_size,
        "quickCheck": "ok",
        "retentionKeepCount": keep,
        "retainedBackups": [str(path) for path in retained],
        "removedFiles": _removed_payload(removed),
        "removedBytes": sum(item.sizeBytes for item in removed),
        "freeBytesBeforeCopy": disk_before.free,
        "requiredFreeBytes": required_free_bytes,
        "freeBytesAfter": disk_after.free,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--backup-dir", required=True, type=Path)
    parser.add_argument("--prefix", default="product_workbench-pre-")
    parser.add_argument(
        "--filename",
        default=f"product_workbench-pre-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3",
    )
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--reserve-ratio", type=float, default=DEFAULT_RESERVE_RATIO)
    parser.add_argument(
        "--min-reserve-bytes",
        type=int,
        default=DEFAULT_MIN_RESERVE_BYTES,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or sys.argv[1:]))
    try:
        result = create_rotating_backup(
            source=args.source,
            backup_dir=args.backup_dir,
            prefix=args.prefix,
            filename=args.filename,
            keep=args.keep,
            reserve_ratio=args.reserve_ratio,
            min_reserve_bytes=args.min_reserve_bytes,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "version": "22.2.6.2",
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
