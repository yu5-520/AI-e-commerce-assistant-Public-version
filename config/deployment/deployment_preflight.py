#!/usr/bin/env python3
"""V22.5.4 deployment storage preflight.

Runs while the production service is still online. It removes stale deployment
state that is not referenced by ``current``, rotates old deployment backups and
checks that the live SQLite database can be backed up with a post-backup safety
reserve. The active candidate directory is explicitly protected.

This module intentionally remains Python 3.6 compatible because it may run with
the server bootstrap interpreter before the sealed application environment is
selected.
"""
from __future__ import print_function

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

VERSION = "22.5.4"
DEFAULT_MIN_FREE_BYTES = 512 * 1024 * 1024
BACKUP_PREFIX = "product_workbench-pre-"


def _size(path):
    path = Path(path)
    if not path.exists():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(str(path)):
        for name in files:
            candidate = Path(root) / name
            try:
                if not candidate.is_symlink():
                    total += candidate.stat().st_size
            except OSError:
                pass
    return total


def _resolved(path):
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path).absolute())


def _remove(path):
    path = Path(path)
    before = _size(path)
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(str(path))
    return before


def _sqlite_quick_check(path):
    if not Path(path).is_file():
        return "missing"
    uri = "file:{0}?mode=ro".format(Path(path).resolve())
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0] if row else "missing")
    finally:
        connection.close()


def _rotate_backups(backup_dir, keep_existing):
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    candidates = []
    for path in backup_dir.glob(BACKUP_PREFIX + "*.sqlite3"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            pass
    candidates.sort(reverse=True)
    removed = []
    freed = 0
    for _mtime, path in candidates[max(0, keep_existing):]:
        size = _remove(path)
        freed += size
        removed.append({"path": str(path), "bytes": size})
    return removed, freed


def run(args):
    root = Path(args.root).resolve()
    releases = root / "releases"
    shared = root / "shared"
    live_db = Path(args.database or shared / "logs" / "product_workbench.sqlite3")
    backup_dir = Path(args.backup_dir or shared / "logs" / "deployment_backups")
    active_candidate = _resolved(args.active_candidate) if args.active_candidate else ""

    releases.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    current_target = ""
    current_link = root / "current"
    if current_link.is_symlink():
        current_target = _resolved(current_link)

    removed_incoming = []
    removed_releases = []
    freed = 0

    for path in releases.glob(".incoming-*"):
        if active_candidate and _resolved(path) == active_candidate:
            continue
        size = _remove(path)
        freed += size
        removed_incoming.append({"path": str(path), "bytes": size})

    # Before a new switch, the current release is the only rollback authority.
    # Any other release directory is an unreferenced historical/failed candidate.
    for path in releases.iterdir():
        if not path.is_dir() or path.name.startswith(".incoming-"):
            continue
        if current_target and _resolved(path) == current_target:
            continue
        size = _remove(path)
        freed += size
        removed_releases.append({"path": str(path), "bytes": size})

    keep_existing_backups = max(0, int(args.backup_keep_count) - 1)
    removed_backups, backup_freed = _rotate_backups(
        backup_dir,
        keep_existing_backups,
    )
    freed += backup_freed

    quick_check = _sqlite_quick_check(live_db)
    database_bytes = _size(live_db)
    candidate_bytes = _size(active_candidate) if active_candidate else 0
    disk = shutil.disk_usage(str(root))
    minimum_free_after_backup = int(args.min_free_bytes)
    required_before_backup = database_bytes + minimum_free_after_backup
    enough = disk.free >= required_before_backup and quick_check in ("ok", "missing")

    result = {
        "version": VERSION,
        "status": "ready" if enough else "failed",
        "errorType": None if enough else "InsufficientDiskSpaceBeforeServiceDowntime",
        "root": str(root),
        "currentTarget": current_target or None,
        "activeCandidate": active_candidate or None,
        "candidateBytes": candidate_bytes,
        "databasePath": str(live_db),
        "databaseBytes": database_bytes,
        "databaseQuickCheck": quick_check,
        "minimumFreeAfterBackupBytes": minimum_free_after_backup,
        "requiredBeforeBackupBytes": required_before_backup,
        "freeBytes": disk.free,
        "shortageBytes": max(0, required_before_backup - disk.free),
        "freedBytes": freed,
        "removedIncoming": removed_incoming,
        "removedUnreferencedReleases": removed_releases,
        "removedDeploymentBackups": removed_backups,
        "serviceDowntimeStarted": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if enough else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--active-candidate")
    parser.add_argument("--database")
    parser.add_argument("--backup-dir")
    parser.add_argument("--backup-keep-count", type=int, default=1)
    parser.add_argument(
        "--min-free-bytes",
        type=int,
        default=int(os.environ.get("AI_DEPLOYMENT_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES)),
    )
    args = parser.parse_args()
    if args.backup_keep_count < 1:
        parser.error("--backup-keep-count must be positive")
    if args.min_free_bytes < 0:
        parser.error("--min-free-bytes must not be negative")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
