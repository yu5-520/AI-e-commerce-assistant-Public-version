#!/usr/bin/env python3
"""Create a deterministic SQLite data-lineage identity without mutating the database."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def database_identity(path: Path, *, include_content_hash: bool = False) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    base: dict[str, Any] = {
        "schema": "sqlite.data-identity.v1",
        "databasePath": str(resolved),
        "exists": resolved.is_file(),
        "quickCheck": None,
        "schemaHash": None,
        "stateHash": None,
        "contentHash": None,
        "fileSize": 0,
        "mtimeNs": None,
        "walSize": 0,
        "shmSize": 0,
        "pageCount": 0,
        "freelistCount": 0,
        "userVersion": 0,
        "applicationId": 0,
        "tableCount": 0,
        "verified": False,
        "errors": [],
    }
    if not resolved.is_file():
        base["errors"] = ["database_missing"]
        return base

    stat = resolved.stat()
    wal = Path(str(resolved) + "-wal")
    shm = Path(str(resolved) + "-shm")
    base.update(
        fileSize=stat.st_size,
        mtimeNs=stat.st_mtime_ns,
        walSize=wal.stat().st_size if wal.is_file() else 0,
        shmSize=shm.stat().st_size if shm.is_file() else 0,
    )
    try:
        uri = "file:{0}?mode=ro".format(resolved.as_posix())
        with sqlite3.connect(uri, uri=True, timeout=3.0) as conn:
            quick = conn.execute("PRAGMA quick_check").fetchone()
            schema_rows = conn.execute(
                "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name"
            ).fetchall()
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            freelist_count = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            application_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
        schema_payload = [list(row) for row in schema_rows]
        schema_hash = canonical_hash({"objects": schema_payload})
        state_payload = {
            "schemaHash": schema_hash,
            "fileSize": base["fileSize"],
            "mtimeNs": base["mtimeNs"],
            "walSize": base["walSize"],
            "pageCount": page_count,
            "freelistCount": freelist_count,
            "userVersion": user_version,
            "applicationId": application_id,
            "tableCount": sum(1 for row in schema_rows if row[0] == "table"),
        }
        base.update(
            quickCheck=str(quick[0] if quick else "unknown"),
            schemaHash=schema_hash,
            stateHash=canonical_hash(state_payload),
            contentHash=sha256_file(resolved) if include_content_hash else None,
            pageCount=page_count,
            freelistCount=freelist_count,
            userVersion=user_version,
            applicationId=application_id,
            tableCount=state_payload["tableCount"],
        )
        base["verified"] = base["quickCheck"] == "ok"
        if not base["verified"]:
            base["errors"].append("sqlite_quick_check_failed")
    except Exception as exc:
        base["errors"].append("sqlite_identity_failed:{0}".format(exc))
    return base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--content-hash", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = database_identity(Path(args.database), include_content_hash=args.content_hash)
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    if not result.get("verified"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
