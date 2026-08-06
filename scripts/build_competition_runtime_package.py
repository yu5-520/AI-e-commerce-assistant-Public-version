#!/usr/bin/env python3
"""Build a deterministic competition runtime package from lineage evidence.

The builder consumes ``runtime-files.txt`` produced by
``compile_competition_lineage.py``. It copies only those files, verifies every
source SHA-256, embeds the lineage proof set, writes a package manifest, and
creates a deterministic ``tar.gz`` archive.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "competition.runtime_package.v1"
EVIDENCE_NAMES = (
    "source-identity.json",
    "registry-snapshot.json",
    "lineage-graph.json",
    "runtime-files.txt",
    "runtime-files.sha256",
    "verification-report.json",
    "evidence-manifest.json",
)


class CompetitionPackageError(RuntimeError):
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


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CompetitionPackageError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise CompetitionPackageError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def safe_runtime_path(raw: str) -> PurePosixPath:
    value = raw.strip()
    if not value:
        raise CompetitionPackageError("EMPTY_RUNTIME_PATH")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise CompetitionPackageError(f"UNSAFE_RUNTIME_PATH:{value}")
    if any(part in {"", ".git", "__pycache__"} for part in path.parts):
        raise CompetitionPackageError(f"FORBIDDEN_RUNTIME_PATH:{value}")
    return path


def parse_sha_file(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        pieces = line.split(None, 1)
        if len(pieces) != 2:
            raise CompetitionPackageError(f"INVALID_SHA_LINE:{path}:{line_number}")
        digest, raw_path = pieces
        runtime_path = safe_runtime_path(raw_path.strip()).as_posix()
        digest = digest.strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise CompetitionPackageError(f"INVALID_SHA256:{path}:{line_number}")
        if runtime_path in result:
            raise CompetitionPackageError(f"DUPLICATE_SHA_PATH:{runtime_path}")
        result[runtime_path] = "sha256:" + digest
    return result


def runtime_paths(lineage_dir: Path) -> list[str]:
    path = lineage_dir / "runtime-files.txt"
    if not path.is_file():
        raise CompetitionPackageError(f"RUNTIME_LIST_MISSING:{path}")
    result: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = safe_runtime_path(line).as_posix()
        if value in seen:
            raise CompetitionPackageError(f"DUPLICATE_RUNTIME_PATH:{value}")
        seen.add(value)
        result.append(value)
    if not result:
        raise CompetitionPackageError("RUNTIME_LIST_EMPTY")
    if result != sorted(result):
        raise CompetitionPackageError("RUNTIME_LIST_NOT_SORTED")
    return result


def assert_within(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise CompetitionPackageError(f"PATH_ESCAPES_ROOT:{path}") from exc


def copy_runtime_files(
    root: Path,
    staging: Path,
    paths: Sequence[str],
    expected_hashes: Mapping[str, str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in paths:
        source = root / relative
        assert_within(root, source)
        if not source.is_file() or source.is_symlink():
            raise CompetitionPackageError(f"RUNTIME_SOURCE_MISSING_OR_UNSAFE:{relative}")
        actual_hash = sha256_file(source)
        expected_hash = expected_hashes.get(relative)
        if expected_hash != actual_hash:
            raise CompetitionPackageError(
                f"RUNTIME_SOURCE_HASH_MISMATCH:{relative}:{actual_hash}:{expected_hash}"
            )
        destination = staging / relative
        assert_within(staging, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        source_mode = stat.S_IMODE(source.stat().st_mode)
        mode = 0o755 if source_mode & 0o111 else 0o644
        destination.chmod(mode)
        records.append(
            {
                "path": relative,
                "sha256": actual_hash,
                "size": destination.stat().st_size,
                "mode": format(mode, "04o"),
            }
        )
    return records


def copy_lineage_evidence(lineage_dir: Path, staging: Path) -> list[dict[str, Any]]:
    evidence_root = staging / "release" / "competition-lineage"
    evidence_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for name in EVIDENCE_NAMES:
        source = lineage_dir / name
        if not source.is_file() or source.is_symlink():
            raise CompetitionPackageError(f"LINEAGE_EVIDENCE_MISSING_OR_UNSAFE:{source}")
        destination = evidence_root / name
        shutil.copyfile(source, destination)
        destination.chmod(0o644)
        records.append(
            {
                "path": f"release/competition-lineage/{name}",
                "sha256": sha256_file(destination),
                "size": destination.stat().st_size,
                "mode": "0644",
            }
        )
    return records


def payload_hash(records: Iterable[Mapping[str, Any]]) -> str:
    material = [
        {
            "path": str(record["path"]),
            "sha256": str(record["sha256"]),
            "size": int(record["size"]),
            "mode": str(record["mode"]),
        }
        for record in sorted(records, key=lambda item: str(item["path"]))
    ]
    return sha256_bytes(canonical_bytes(material))


def deterministic_archive(staging: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix(archive.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as tar:
                for path in sorted(staging.rglob("*"), key=lambda item: item.relative_to(staging).as_posix()):
                    relative = path.relative_to(staging).as_posix()
                    info = tar.gettarinfo(str(path), arcname=relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    info.mtime = 0
                    info.pax_headers = {}
                    if path.is_file():
                        with path.open("rb") as handle:
                            tar.addfile(info, handle)
                    elif path.is_dir():
                        tar.addfile(info)
                    else:
                        raise CompetitionPackageError(f"UNSUPPORTED_PACKAGE_NODE:{relative}")
    os.replace(temporary, archive)


def build_package(
    *,
    root: Path,
    lineage_dir: Path,
    output_dir: Path,
    source_commit: str,
    archive_name: str,
) -> dict[str, Any]:
    verification = read_json(lineage_dir / "verification-report.json")
    evidence_manifest = read_json(lineage_dir / "evidence-manifest.json")
    registry_snapshot = read_json(lineage_dir / "registry-snapshot.json")
    source_identity = read_json(lineage_dir / "source-identity.json")

    if verification.get("verified") is not True:
        raise CompetitionPackageError(
            f"LINEAGE_NOT_VERIFIED:{verification.get('findings')}"
        )
    if str(verification.get("sourceCommit")) != source_commit:
        raise CompetitionPackageError(
            f"SOURCE_COMMIT_MISMATCH:{verification.get('sourceCommit')}:{source_commit}"
        )
    if evidence_manifest.get("verified") is not True:
        raise CompetitionPackageError("EVIDENCE_MANIFEST_NOT_VERIFIED")
    if str(evidence_manifest.get("sourceCommit")) != source_commit:
        raise CompetitionPackageError("EVIDENCE_SOURCE_COMMIT_MISMATCH")

    paths = runtime_paths(lineage_dir)
    expected_hashes = parse_sha_file(lineage_dir / "runtime-files.sha256")
    if set(paths) != set(expected_hashes):
        missing = sorted(set(paths) - set(expected_hashes))
        extra = sorted(set(expected_hashes) - set(paths))
        raise CompetitionPackageError(f"RUNTIME_SHA_SET_MISMATCH:{missing}:{extra}")

    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / archive_name
    with tempfile.TemporaryDirectory(prefix="competition-runtime-") as temp_dir:
        staging = Path(temp_dir) / "bundle"
        staging.mkdir(parents=True)
        runtime_records = copy_runtime_files(root, staging, paths, expected_hashes)
        evidence_records = copy_lineage_evidence(lineage_dir, staging)
        all_payload_records = sorted(
            runtime_records + evidence_records,
            key=lambda item: str(item["path"]),
        )
        calculated_runtime_hash = sha256_bytes(
            canonical_bytes(
                [
                    {
                        "path": item["path"],
                        "sha256": item["sha256"],
                        "size": item["size"],
                    }
                    for item in runtime_records
                ]
            )
        )
        if calculated_runtime_hash != verification.get("runtimeHash"):
            raise CompetitionPackageError(
                "RUNTIME_HASH_MISMATCH:"
                f"{calculated_runtime_hash}:{verification.get('runtimeHash')}"
            )

        manifest_material: dict[str, Any] = {
            "schema": SCHEMA,
            "packageType": "competition-public-runtime",
            "sourceRepository": source_identity.get("publicRepository"),
            "sourceCommit": source_commit,
            "motherRepository": source_identity.get("motherRepository"),
            "motherCommit": source_identity.get("motherCommit"),
            "entrypoint": evidence_manifest.get("entrypoint"),
            "registryVersion": registry_snapshot.get("registryVersion"),
            "registryRootHash": verification.get("registryRootHash"),
            "registrySnapshotHash": evidence_manifest.get("registrySnapshotHash"),
            "lineageGraphHash": verification.get("graphHash"),
            "lineageVerificationHash": verification.get("verificationHash"),
            "runtimeHash": verification.get("runtimeHash"),
            "runtimeFileCount": len(runtime_records),
            "evidenceFileCount": len(evidence_records),
            "payloadFileCount": len(all_payload_records),
            "payloadHash": payload_hash(all_payload_records),
            "runtimeFiles": runtime_records,
            "evidenceFiles": evidence_records,
            "deploymentContract": {
                "immutableDirectory": True,
                "currentSymlinkSwitchRequired": True,
                "environmentVariablesExternal": True,
                "databaseExternal": True,
                "uploadedReportsExternal": True,
                "switchAfterCandidateSmokeOnly": True,
            },
        }
        manifest = {
            **manifest_material,
            "manifestHash": sha256_bytes(canonical_bytes(manifest_material)),
        }
        manifest_path = staging / "release" / "competition-runtime-manifest.json"
        write_json(manifest_path, manifest)
        manifest_path.chmod(0o644)
        deterministic_archive(staging, archive)

    result = {
        "schema": "competition.runtime_package_build.v1",
        "sourceCommit": source_commit,
        "archive": archive.name,
        "archiveSha256": sha256_file(archive),
        "archiveSize": archive.stat().st_size,
        "manifestHash": manifest["manifestHash"],
        "payloadHash": manifest["payloadHash"],
        "runtimeHash": manifest["runtimeHash"],
        "lineageGraphHash": manifest["lineageGraphHash"],
        "runtimeFileCount": manifest["runtimeFileCount"],
        "evidenceFileCount": manifest["evidenceFileCount"],
        "verifiedLineage": True,
    }
    write_json(output_dir / "competition-runtime-build.json", result)
    (output_dir / f"{archive.name}.sha256").write_text(
        f"{result['archiveSha256'].removeprefix('sha256:')}  {archive.name}\n",
        encoding="utf-8",
    )
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a precise competition runtime package.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--lineage-dir", default="dist/competition-lineage")
    parser.add_argument("--output-dir", default="dist/competition-runtime")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--archive-name", default="competition-runtime.tar.gz")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    result = build_package(
        root=root,
        lineage_dir=(root / args.lineage_dir).resolve(),
        output_dir=(root / args.output_dir).resolve(),
        source_commit=args.source_commit.strip(),
        archive_name=args.archive_name,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition runtime package build failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
