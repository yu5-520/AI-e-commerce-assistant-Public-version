"""SQLite-backed immutable competition sample XLSX assets.

The database is the runtime authority for evaluator downloads.  Canonical XLSX bytes
are generated only while the release-owned database state is prepared/sealed, then
stored as BLOBs with a content SHA256.  Download requests read and verify the stored
bytes; they never regenerate a workbook.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, run_with_retry
from src.services.competition_sample_report_service import (
    SAMPLE_REPORT_VERSION,
    SAMPLE_REPORTS,
    build_competition_sample_xlsx,
    sample_report_filename,
)

COMPETITION_SAMPLE_ASSET_TABLE = "competition_sample_assets"
COMPETITION_SAMPLE_ASSET_SCHEMA = "competition.sample_asset.v1"
COMPETITION_SAMPLE_ASSET_VERSION = "1.0.0"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _asset_id(period: int) -> str:
    return f"competition-sample-period-{period}-v{COMPETITION_SAMPLE_ASSET_VERSION}"


def _expected_asset(period: int) -> Dict[str, Any]:
    payload = build_competition_sample_xlsx(period)
    return {
        "assetId": _asset_id(period),
        "period": period,
        "filename": sample_report_filename(period),
        "mimeType": XLSX_MEDIA_TYPE,
        "content": payload,
        "contentSha256": _sha256(payload),
        "byteSize": len(payload),
        "schemaVersion": COMPETITION_SAMPLE_ASSET_SCHEMA,
        "sampleVersion": SAMPLE_REPORT_VERSION,
        "active": True,
    }


def _validate_asset_row(row: Any, *, expected: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if row is None:
        raise LookupError("competition_sample_asset_not_found")
    payload = bytes(row["content_blob"] or b"")
    actual_hash = _sha256(payload)
    stored_hash = str(row["content_sha256"] or "")
    byte_size = int(row["byte_size"] or 0)
    if not payload.startswith(b"PK"):
        raise RuntimeError("competition_sample_asset_not_openxml")
    if actual_hash != stored_hash:
        raise RuntimeError("competition_sample_asset_hash_mismatch")
    if len(payload) != byte_size:
        raise RuntimeError("competition_sample_asset_size_mismatch")
    result = {
        "assetId": str(row["asset_id"]),
        "period": int(row["period"]),
        "filename": str(row["filename"]),
        "mimeType": str(row["mime_type"]),
        "content": payload,
        "contentSha256": stored_hash,
        "byteSize": byte_size,
        "schemaVersion": str(row["schema_version"]),
        "sampleVersion": str(row["sample_version"]),
        "createdAt": str(row["created_at"]),
        "active": bool(row["active"]),
    }
    if expected is not None:
        immutable_fields = (
            "assetId",
            "period",
            "filename",
            "mimeType",
            "contentSha256",
            "byteSize",
            "schemaVersion",
            "sampleVersion",
            "active",
        )
        mismatches = [
            key for key in immutable_fields if result.get(key) != expected.get(key)
        ]
        if payload != expected.get("content"):
            mismatches.append("content")
        if mismatches:
            raise RuntimeError(
                "competition_sample_asset_immutable_seed_mismatch:"
                + ",".join(sorted(set(mismatches)))
            )
    return result


def ensure_competition_sample_assets() -> Dict[str, Any]:
    """Create and seed the immutable system-asset table idempotently."""

    def _operation() -> Dict[str, Any]:
        seeded = 0
        verified = 0
        hashes: Dict[str, str] = {}
        with connect() as conn:
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {COMPETITION_SAMPLE_ASSET_TABLE} (
                    asset_id TEXT PRIMARY KEY,
                    period INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    content_blob BLOB NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    schema_version TEXT NOT NULL,
                    sample_version TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    UNIQUE(period, schema_version)
                )
                """
            )
            conn.execute(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_competition_sample_assets_active_period
                ON {COMPETITION_SAMPLE_ASSET_TABLE}(period)
                WHERE active = 1
                """
            )
            for period in sorted(SAMPLE_REPORTS):
                expected = _expected_asset(period)
                row = conn.execute(
                    f"SELECT * FROM {COMPETITION_SAMPLE_ASSET_TABLE} WHERE asset_id=?",
                    (expected["assetId"],),
                ).fetchone()
                if row is None:
                    active_row = conn.execute(
                        f"SELECT asset_id FROM {COMPETITION_SAMPLE_ASSET_TABLE} WHERE period=? AND active=1",
                        (period,),
                    ).fetchone()
                    if active_row is not None:
                        raise RuntimeError(
                            "competition_sample_asset_active_version_conflict:"
                            f"period={period}:asset={active_row['asset_id']}"
                        )
                    conn.execute(
                        f"""
                        INSERT INTO {COMPETITION_SAMPLE_ASSET_TABLE} (
                            asset_id, period, filename, mime_type, content_blob,
                            content_sha256, byte_size, schema_version, sample_version, active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            expected["assetId"],
                            period,
                            expected["filename"],
                            expected["mimeType"],
                            expected["content"],
                            expected["contentSha256"],
                            expected["byteSize"],
                            expected["schemaVersion"],
                            expected["sampleVersion"],
                        ),
                    )
                    row = conn.execute(
                        f"SELECT * FROM {COMPETITION_SAMPLE_ASSET_TABLE} WHERE asset_id=?",
                        (expected["assetId"],),
                    ).fetchone()
                    seeded += 1
                _validate_asset_row(row, expected=expected)
                verified += 1
                hashes[str(period)] = expected["contentSha256"]
            conn.commit()
        return {
            "schema": COMPETITION_SAMPLE_ASSET_SCHEMA,
            "version": COMPETITION_SAMPLE_ASSET_VERSION,
            "table": COMPETITION_SAMPLE_ASSET_TABLE,
            "assetCount": verified,
            "seededCount": seeded,
            "verifiedCount": verified,
            "contentSha256ByPeriod": hashes,
            "runtimeDownloadAuthority": "sqlite_blob",
            "runtimeWorkbookGenerationAllowed": False,
            "resetProtected": True,
        }

    return run_with_retry(_operation)


def get_competition_sample_asset(period: int) -> Dict[str, Any]:
    """Read one active asset from SQLite and verify it before returning bytes."""
    if period not in SAMPLE_REPORTS:
        raise LookupError("competition_sample_period_not_found")

    def _operation() -> Dict[str, Any]:
        with connect() as conn:
            row = conn.execute(
                f"SELECT * FROM {COMPETITION_SAMPLE_ASSET_TABLE} WHERE period=? AND active=1",
                (period,),
            ).fetchone()
        return _validate_asset_row(row)

    return run_with_retry(_operation)


def list_competition_sample_asset_metadata() -> List[Dict[str, Any]]:
    def _operation() -> List[Dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM {COMPETITION_SAMPLE_ASSET_TABLE} WHERE active=1 ORDER BY period"
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            asset = _validate_asset_row(row)
            asset.pop("content", None)
            result.append(asset)
        return result

    return run_with_retry(_operation)


__all__ = [
    "COMPETITION_SAMPLE_ASSET_TABLE",
    "COMPETITION_SAMPLE_ASSET_SCHEMA",
    "COMPETITION_SAMPLE_ASSET_VERSION",
    "XLSX_MEDIA_TYPE",
    "ensure_competition_sample_assets",
    "get_competition_sample_asset",
    "list_competition_sample_asset_metadata",
]
