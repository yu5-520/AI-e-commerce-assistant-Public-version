#!/usr/bin/env python3
"""Verify a competition runtime ``tar.gz`` without trusting its contents."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


MANIFEST_PATH = "release/competition-runtime-manifest.json"


class CompetitionPackageVerificationError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def safe_member_name(raw: str) -> str:
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise CompetitionPackageVerificationError(f"UNSAFE_ARCHIVE_PATH:{raw}")
    return path.as_posix()


def record_material(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": str(record["path"]),
        "sha256": str(record["sha256"]),
        "size": int(record["size"]),
        "mode": str(record["mode"]),
    }


def verify_archive(path: Path, expected_source_commit: str | None) -> dict[str, Any]:
    if not path.is_file():
        raise CompetitionPackageVerificationError(f"ARCHIVE_MISSING:{path}")

    archive_hash = sha256_file(path)
    member_bytes: dict[str, bytes] = {}
    member_modes: dict[str, str] = {}
    with tarfile.open(path, mode="r:gz") as tar:
        for member in tar.getmembers():
            name = safe_member_name(member.name)
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise CompetitionPackageVerificationError(f"UNSAFE_ARCHIVE_MEMBER:{name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise CompetitionPackageVerificationError(f"UNSUPPORTED_ARCHIVE_MEMBER:{name}")
            if name in member_bytes:
                raise CompetitionPackageVerificationError(f"DUPLICATE_ARCHIVE_MEMBER:{name}")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise CompetitionPackageVerificationError(f"ARCHIVE_MEMBER_UNREADABLE:{name}")
            data = extracted.read()
            member_bytes[name] = data
            member_modes[name] = format(member.mode & 0o7777, "04o")

    if MANIFEST_PATH not in member_bytes:
        raise CompetitionPackageVerificationError("PACKAGE_MANIFEST_MISSING")
    try:
        manifest = json.loads(member_bytes[MANIFEST_PATH].decode("utf-8"))
    except Exception as exc:
        raise CompetitionPackageVerificationError(f"PACKAGE_MANIFEST_INVALID:{exc}") from exc
    if not isinstance(manifest, dict):
        raise CompetitionPackageVerificationError("PACKAGE_MANIFEST_OBJECT_REQUIRED")
    if manifest.get("schema") != "competition.runtime_package.v1":
        raise CompetitionPackageVerificationError(
            f"PACKAGE_SCHEMA_UNSUPPORTED:{manifest.get('schema')}"
        )
    if expected_source_commit and manifest.get("sourceCommit") != expected_source_commit:
        raise CompetitionPackageVerificationError(
            f"SOURCE_COMMIT_MISMATCH:{manifest.get('sourceCommit')}:{expected_source_commit}"
        )

    manifest_material = dict(manifest)
    declared_manifest_hash = str(manifest_material.pop("manifestHash", ""))
    calculated_manifest_hash = sha256_bytes(canonical_bytes(manifest_material))
    if declared_manifest_hash != calculated_manifest_hash:
        raise CompetitionPackageVerificationError(
            f"MANIFEST_HASH_MISMATCH:{calculated_manifest_hash}:{declared_manifest_hash}"
        )

    runtime_records = manifest.get("runtimeFiles")
    evidence_records = manifest.get("evidenceFiles")
    if not isinstance(runtime_records, list) or not isinstance(evidence_records, list):
        raise CompetitionPackageVerificationError("PACKAGE_FILE_RECORDS_REQUIRED")
    declared_records = runtime_records + evidence_records
    expected_paths: set[str] = {MANIFEST_PATH}
    normalized_records: list[dict[str, Any]] = []
    for raw_record in declared_records:
        if not isinstance(raw_record, dict):
            raise CompetitionPackageVerificationError("PACKAGE_FILE_RECORD_OBJECT_REQUIRED")
        record = record_material(raw_record)
        name = safe_member_name(record["path"])
        if name == MANIFEST_PATH:
            raise CompetitionPackageVerificationError("MANIFEST_CANNOT_DECLARE_ITSELF")
        if name in expected_paths:
            raise CompetitionPackageVerificationError(f"DUPLICATE_PACKAGE_RECORD:{name}")
        expected_paths.add(name)
        data = member_bytes.get(name)
        if data is None:
            raise CompetitionPackageVerificationError(f"DECLARED_FILE_MISSING:{name}")
        actual_hash = sha256_bytes(data)
        if actual_hash != record["sha256"]:
            raise CompetitionPackageVerificationError(
                f"FILE_HASH_MISMATCH:{name}:{actual_hash}:{record['sha256']}"
            )
        if len(data) != record["size"]:
            raise CompetitionPackageVerificationError(
                f"FILE_SIZE_MISMATCH:{name}:{len(data)}:{record['size']}"
            )
        actual_mode = member_modes[name]
        if actual_mode != record["mode"]:
            raise CompetitionPackageVerificationError(
                f"FILE_MODE_MISMATCH:{name}:{actual_mode}:{record['mode']}"
            )
        normalized_records.append(record)

    actual_paths = set(member_bytes)
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise CompetitionPackageVerificationError(f"PACKAGE_FILE_SET_MISMATCH:{missing}:{extra}")

    calculated_payload_hash = sha256_bytes(
        canonical_bytes(sorted(normalized_records, key=lambda item: item["path"]))
    )
    if calculated_payload_hash != manifest.get("payloadHash"):
        raise CompetitionPackageVerificationError(
            f"PAYLOAD_HASH_MISMATCH:{calculated_payload_hash}:{manifest.get('payloadHash')}"
        )
    if manifest.get("runtimeFileCount") != len(runtime_records):
        raise CompetitionPackageVerificationError("RUNTIME_FILE_COUNT_MISMATCH")
    if manifest.get("evidenceFileCount") != len(evidence_records):
        raise CompetitionPackageVerificationError("EVIDENCE_FILE_COUNT_MISMATCH")
    if manifest.get("payloadFileCount") != len(declared_records):
        raise CompetitionPackageVerificationError("PAYLOAD_FILE_COUNT_MISMATCH")
    if manifest.get("entrypoint") != "src.api.main:app":
        raise CompetitionPackageVerificationError(
            f"UNEXPECTED_ENTRYPOINT:{manifest.get('entrypoint')}"
        )

    lineage_report_path = "release/competition-lineage/verification-report.json"
    lineage_manifest_path = "release/competition-lineage/evidence-manifest.json"
    try:
        lineage_report = json.loads(member_bytes[lineage_report_path].decode("utf-8"))
        lineage_manifest = json.loads(member_bytes[lineage_manifest_path].decode("utf-8"))
    except Exception as exc:
        raise CompetitionPackageVerificationError(f"LINEAGE_EVIDENCE_INVALID:{exc}") from exc
    if lineage_report.get("verified") is not True or lineage_manifest.get("verified") is not True:
        raise CompetitionPackageVerificationError("EMBEDDED_LINEAGE_NOT_VERIFIED")
    if lineage_report.get("sourceCommit") != manifest.get("sourceCommit"):
        raise CompetitionPackageVerificationError("EMBEDDED_SOURCE_COMMIT_MISMATCH")
    if lineage_report.get("runtimeHash") != manifest.get("runtimeHash"):
        raise CompetitionPackageVerificationError("EMBEDDED_RUNTIME_HASH_MISMATCH")
    if lineage_report.get("graphHash") != manifest.get("lineageGraphHash"):
        raise CompetitionPackageVerificationError("EMBEDDED_GRAPH_HASH_MISMATCH")

    return {
        "schema": "competition.runtime_package_verification.v1",
        "verified": True,
        "archive": path.name,
        "archiveSha256": archive_hash,
        "sourceCommit": manifest.get("sourceCommit"),
        "entrypoint": manifest.get("entrypoint"),
        "manifestHash": declared_manifest_hash,
        "payloadHash": manifest.get("payloadHash"),
        "runtimeHash": manifest.get("runtimeHash"),
        "lineageGraphHash": manifest.get("lineageGraphHash"),
        "runtimeFileCount": len(runtime_records),
        "evidenceFileCount": len(evidence_records),
        "archiveFileCount": len(member_bytes),
        "noUndeclaredFiles": True,
        "noUnsafeArchiveMembers": True,
        "embeddedLineageVerified": True,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a competition runtime package.")
    parser.add_argument("archive")
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--output", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = verify_archive(Path(args.archive).resolve(), args.source_commit)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition runtime package verification failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
