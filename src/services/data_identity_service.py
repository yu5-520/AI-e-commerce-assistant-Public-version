"""V22.4 SQLite data identity and deployment-lineage projection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from scripts.sqlite_data_identity import database_identity
from src.repositories.sqlite_repository import DB_PATH

DATA_IDENTITY_VERSION = "22.4.0.1"
ROOT = Path(__file__).resolve().parents[2]
LINEAGE_PATH = ROOT / "data" / "release-data-lineage.json"


def data_identity(*, include_content_hash: bool = False) -> Dict[str, Any]:
    live = database_identity(Path(DB_PATH), include_content_hash=include_content_hash)
    lineage: Dict[str, Any] | None = None
    lineage_errors: list[str] = []
    if LINEAGE_PATH.is_file():
        try:
            loaded = json.loads(LINEAGE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                lineage = loaded
            else:
                lineage_errors.append("data_lineage_must_be_object")
        except Exception as exc:
            lineage_errors.append(f"data_lineage_read_failed:{exc}")
    else:
        lineage_errors.append("data_lineage_missing")

    expected_schema_hash = (lineage or {}).get("schemaHash")
    schema_match = bool(expected_schema_hash and expected_schema_hash == live.get("schemaHash"))
    return {
        "schema": "data.identity.v1",
        "version": DATA_IDENTITY_VERSION,
        "database": live,
        "lineage": lineage,
        "lineagePath": str(LINEAGE_PATH),
        "lineagePresent": lineage is not None,
        "schemaMatch": schema_match,
        "releaseHash": (lineage or {}).get("releaseHash"),
        "sourceCommit": (lineage or {}).get("sourceCommit"),
        "backupContentHash": (lineage or {}).get("backupContentHash"),
        "verified": bool(live.get("verified") and lineage is not None and schema_match and not lineage_errors),
        "errors": list(live.get("errors") or []) + lineage_errors + ([] if schema_match or not expected_schema_hash else ["data_schema_hash_mismatch"]),
    }


__all__ = ["DATA_IDENTITY_VERSION", "LINEAGE_PATH", "data_identity"]
