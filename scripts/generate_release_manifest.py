#!/usr/bin/env python3
"""Generate one deterministic V22.4 release manifest and sealed staging bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PREFIX = Path("release") / "attestation"
REQUIRED_EVIDENCE_PATHS = (
    "compile-syntax.log",
    "static-contract.log",
    "app-route-smoke.log",
    "pytest.log",
    "production-runtime-verification.json",
    "attested-files.sha256",
    "test-attestation.json",
    "pip-freeze.txt",
    "python-runtime.json",
)
REQUIRED_TEST_ATTESTATION_FLAGS = (
    "staticCheckerImportedRuntime",
    "cleanProcessRouteSmokePassed",
    "dependencyLockVerified",
    "cleanProductionRuntimeVerified",
    "python36BootstrapChecked",
    "rootVerifierPinningChecked",
    "ordinaryVerifierRotationRejected",
    "compilePassed",
    "shellSyntaxPassed",
    "frontendSyntaxPassed",
    "staticContractPassed",
    "pytestPassed",
    "sqliteBackupLifecycleChecked",
    "legacyBranchDeployAbsent",
    "shadowServiceRetirementChecked",
    "strayRuntimeRetirementChecked",
    "forbiddenLegacyPathCleanupChecked",
    "legacyWorkingTreeRetirementChecked",
    "externalBundleControllerChecked",
    "attestedSourcesSealed",
    "testEvidenceSealed",
    "testLogsSealed",
)


def canonical_bytes(manifest: Dict[str, Any]) -> bytes:
    payload = {k: v for k, v in manifest.items() if k not in {"manifestHash", "releaseHash"}}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"JSON object required: {path}")
    return payload


def expand(root: Path, patterns: Iterable[str], excludes: Iterable[str]) -> list[Path]:
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(path for path in root.glob(str(pattern)) if path.is_file())
    excluded: set[Path] = set()
    for pattern in excludes:
        excluded.update(path for path in root.glob(str(pattern)) if path.is_file())
    return sorted(paths - excluded, key=lambda item: item.relative_to(root).as_posix())


def file_entries(root: Path, paths: Iterable[Path]) -> list[Dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in paths
    ]


def preserve_runtime_python_entry(value: str) -> Path:
    """Return an absolute executable entry path without following venv symlinks."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.absolute()


def runtime_environment_identity(
    runtime_python: Path,
    dependency_lock: Path,
    checker: Path,
    evidence_dir: Path,
    expected_python_version: str,
) -> tuple[str, str]:
    if not runtime_python.is_file():
        raise SystemExit(f"runtime Python missing: {runtime_python}")

    probe = subprocess.run(
        [
            str(runtime_python),
            "-c",
            (
                "import json,sys;"
                "print(json.dumps({"
                "'executable':sys.executable,"
                "'prefix':sys.prefix,"
                "'basePrefix':sys.base_prefix"
                "},sort_keys=True))"
            ),
        ],
        text=True,
        capture_output=True,
    )
    if probe.returncode != 0:
        raise SystemExit("runtime Python identity probe failed\n" + probe.stdout + probe.stderr)
    try:
        runtime_probe = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"runtime Python identity probe returned invalid JSON: {exc}") from exc
    if runtime_probe.get("prefix") == runtime_probe.get("basePrefix"):
        raise SystemExit(
            "release runtime Python is not isolated: "
            + json.dumps(runtime_probe, ensure_ascii=False, sort_keys=True)
        )

    freeze_path = evidence_dir / "pip-freeze.txt"
    completed = subprocess.run(
        [
            str(runtime_python),
            str(checker),
            str(dependency_lock),
            "--strict",
            "--write-freeze",
            str(freeze_path),
        ],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise SystemExit(
            "runtime dependency verification failed\n"
            + completed.stdout
            + completed.stderr
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"runtime dependency verification returned invalid JSON: {exc}") from exc
    if result.get("verified") is not True:
        raise SystemExit(f"runtime dependency verification failed: {result}")
    python_version = str(result.get("pythonVersion") or "")
    pip_freeze_hash = str(result.get("pipFreezeHash") or "")
    if python_version != expected_python_version:
        raise SystemExit(
            f"release runtime Python mismatch: expected {expected_python_version}, got {python_version}"
        )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", pip_freeze_hash):
        raise SystemExit(f"invalid runtime environment hash: {pip_freeze_hash}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "python-runtime.json").write_text(
        json.dumps(
            {
                "schema": "release.python-runtime.v1",
                "pythonVersion": python_version,
                "pipFreezeHash": pip_freeze_hash,
                "lockedPackageCount": result.get("lockedPackageCount"),
                "installedPackageCount": result.get("installedPackageCount"),
                "dependencyLockVerified": True,
                "strictExtras": True,
                "isolatedRuntime": True,
                "runtimeExecutable": runtime_probe.get("executable"),
                "runtimePrefix": runtime_probe.get("prefix"),
                "runtimeBasePrefix": runtime_probe.get("basePrefix"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return python_version, pip_freeze_hash


def require_evidence_floor(evidence_dir: Path) -> None:
    missing: list[str] = []
    empty: list[str] = []
    for relative in REQUIRED_EVIDENCE_PATHS:
        path = evidence_dir / relative
        if not path.is_file():
            missing.append(relative)
        elif path.stat().st_size <= 0:
            empty.append(relative)
    if missing or empty:
        raise SystemExit(
            "release gray-test evidence floor is incomplete: "
            + json.dumps(
                {"missing": missing, "empty": empty},
                ensure_ascii=False,
                sort_keys=True,
            )
        )


def expected_attested_digest(root: Path, attested_paths: Iterable[Path]) -> str:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}"
        for path in sorted(attested_paths, key=lambda item: item.relative_to(root).as_posix())
    ]
    if not lines:
        raise SystemExit("release attested file set is empty")
    return "\n".join(lines) + "\n"


def validate_evidence_semantics(
    evidence_dir: Path,
    *,
    source_commit: str,
    expected_python_version: str,
    pip_freeze_hash: str,
    expected_attested_digest_text: str,
) -> Dict[str, Any]:
    errors: list[str] = []

    test_attestation = load_json(evidence_dir / "test-attestation.json")
    if test_attestation.get("schema") != "release.test-attestation.v1":
        errors.append("test_attestation_schema_mismatch")
    if test_attestation.get("sourceCommit") != source_commit:
        errors.append("test_attestation_source_commit_mismatch")
    if test_attestation.get("grayPythonVersion") != expected_python_version:
        errors.append("test_attestation_gray_python_mismatch")
    if test_attestation.get("productionPythonVersion") != expected_python_version:
        errors.append("test_attestation_production_python_mismatch")
    if sorted(str(item) for item in test_attestation.get("requiredEvidenceFiles") or []) != sorted(
        REQUIRED_EVIDENCE_PATHS
    ):
        errors.append("test_attestation_required_evidence_mismatch")
    for flag in REQUIRED_TEST_ATTESTATION_FLAGS:
        expected = False if flag == "staticCheckerImportedRuntime" else True
        if test_attestation.get(flag) is not expected:
            errors.append(f"test_attestation_flag_mismatch:{flag}")

    production_runtime = load_json(evidence_dir / "production-runtime-verification.json")
    if production_runtime.get("schema") != "dependency.lock.verification.v1":
        errors.append("production_runtime_schema_mismatch")
    if production_runtime.get("verified") is not True:
        errors.append("production_runtime_not_verified")
    if production_runtime.get("strictExtras") is not True:
        errors.append("production_runtime_not_strict")
    if production_runtime.get("pythonVersion") != expected_python_version:
        errors.append("production_runtime_python_mismatch")
    if production_runtime.get("pipFreezeHash") != pip_freeze_hash:
        errors.append("production_runtime_environment_hash_mismatch")
    for field in ("missing", "mismatched", "extras"):
        if production_runtime.get(field) not in ([], None):
            errors.append(f"production_runtime_{field}_not_empty")

    python_runtime = load_json(evidence_dir / "python-runtime.json")
    if python_runtime.get("schema") != "release.python-runtime.v1":
        errors.append("python_runtime_schema_mismatch")
    if python_runtime.get("pythonVersion") != expected_python_version:
        errors.append("python_runtime_version_mismatch")
    if python_runtime.get("pipFreezeHash") != pip_freeze_hash:
        errors.append("python_runtime_environment_hash_mismatch")
    if python_runtime.get("dependencyLockVerified") is not True:
        errors.append("python_runtime_dependency_lock_not_verified")
    if python_runtime.get("strictExtras") is not True:
        errors.append("python_runtime_not_strict")
    if python_runtime.get("isolatedRuntime") is not True:
        errors.append("python_runtime_not_isolated")

    actual_freeze_hash = "sha256:" + sha256_file(evidence_dir / "pip-freeze.txt")
    if actual_freeze_hash != pip_freeze_hash:
        errors.append("pip_freeze_evidence_hash_mismatch")

    actual_attested_digest = (evidence_dir / "attested-files.sha256").read_text(encoding="utf-8")
    if actual_attested_digest != expected_attested_digest_text:
        errors.append("attested_source_digest_mismatch")

    if errors:
        raise SystemExit(
            "release evidence semantic binding failed: "
            + json.dumps(errors, ensure_ascii=False, sort_keys=True)
        )
    return {
        "schema": "release.evidence-binding.v1",
        "sourceCommit": source_commit,
        "pythonVersion": expected_python_version,
        "pipFreezeHash": pip_freeze_hash,
        "requiredEvidenceFileCount": len(REQUIRED_EVIDENCE_PATHS),
        "attestedSourceCount": len(expected_attested_digest_text.splitlines()),
        "verified": True,
    }


def sealed_evidence_entries(evidence_dir: Path) -> tuple[list[Dict[str, Any]], list[tuple[Path, Path]]]:
    sources = sorted(path for path in evidence_dir.rglob("*") if path.is_file())
    if not sources:
        raise SystemExit("test evidence directory must contain at least one file")
    entries: list[Dict[str, Any]] = []
    copies: list[tuple[Path, Path]] = []
    for source in sources:
        relative = source.relative_to(evidence_dir)
        destination_relative = EVIDENCE_PREFIX / relative
        entries.append(
            {
                "path": destination_relative.as_posix(),
                "sha256": sha256_file(source),
                "size": source.stat().st_size,
            }
        )
        copies.append((source, destination_relative))
    return entries, copies


def test_run_hash(entries: Iterable[Dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    normalized = sorted(entries, key=lambda item: str(item.get("path") or ""))
    if not normalized:
        raise SystemExit("test evidence entries are required")
    for entry in normalized:
        path = str(entry.get("path") or "")
        file_hash = str(entry.get("sha256") or "")
        if not path or len(file_hash) != 64:
            raise SystemExit(f"invalid test evidence entry: {entry}")
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
    return "sha256:" + digest.hexdigest()


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def copy_staging(
    root: Path,
    staging: Path,
    runtime_paths: Iterable[Path],
    attested_paths: Iterable[Path],
    evidence_copies: Iterable[tuple[Path, Path]],
    manifest_path: Path,
) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    copied: set[str] = set()
    for source in list(runtime_paths) + list(attested_paths):
        relative = source.relative_to(root)
        key = relative.as_posix()
        if key in copied:
            continue
        copy_file(source, staging / relative)
        copied.add(key)
    for source, destination_relative in evidence_copies:
        key = destination_relative.as_posix()
        if key in copied:
            raise SystemExit(f"release evidence path collision: {key}")
        copy_file(source, staging / destination_relative)
        copied.add(key)
    copy_file(manifest_path, staging / "release" / "release-manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--policy", default="release/release-policy.json")
    parser.add_argument("--output", default="release/release-manifest.json")
    parser.add_argument("--dependency-lock", default="requirements.lock")
    parser.add_argument("--runtime-python", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--test-evidence-dir", required=True)
    parser.add_argument("--staging-dir")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if len(args.source_commit) != 40 or any(ch not in "0123456789abcdef" for ch in args.source_commit):
        raise SystemExit("--source-commit must be a lowercase 40-character Git commit SHA")
    policy = load_json(root / args.policy)
    expected_python_version = str(policy.get("releasePythonVersion") or "")
    if not re.fullmatch(r"3\.11\.[0-9]+", expected_python_version):
        raise SystemExit("release-policy.json must declare an exact releasePythonVersion")
    dependency_lock = (root / args.dependency_lock).resolve()
    if not dependency_lock.is_file():
        raise SystemExit(f"dependency lock missing: {dependency_lock}")
    if dependency_lock.relative_to(root).as_posix() != "requirements.lock":
        raise SystemExit("V22.4 production dependency lock must be requirements.lock")

    evidence_dir = Path(args.test_evidence_dir).resolve()
    python_version, pip_freeze_hash = runtime_environment_identity(
        preserve_runtime_python_entry(args.runtime_python),
        dependency_lock,
        root / "scripts" / "check_dependency_lock.py",
        evidence_dir,
        expected_python_version,
    )
    require_evidence_floor(evidence_dir)

    runtime_paths = expand(root, policy.get("runtimeGlobs") or [], policy.get("excludeGlobs") or [])
    runtime_paths = [path for path in runtime_paths if path.relative_to(root).as_posix() != args.output]
    attested_paths = expand(root, policy.get("attestedGlobs") or [], policy.get("excludeGlobs") or [])
    if not runtime_paths:
        raise SystemExit("release runtime file set is empty")
    if not attested_paths:
        raise SystemExit("release attested file set is empty")

    evidence_binding = validate_evidence_semantics(
        evidence_dir,
        source_commit=args.source_commit,
        expected_python_version=python_version,
        pip_freeze_hash=pip_freeze_hash,
        expected_attested_digest_text=expected_attested_digest(root, attested_paths),
    )
    evidence_entries, evidence_copies = sealed_evidence_entries(evidence_dir)

    manifest: Dict[str, Any] = {
        "schema": "release.manifest.v1",
        "productVersion": str(policy.get("productVersion") or ""),
        "runtimeMode": str(policy.get("runtimeMode") or "single_release_sealed_runtime"),
        "sourceCommit": args.source_commit,
        "runtimeFiles": file_entries(root, runtime_paths),
        "attestedFiles": file_entries(root, attested_paths),
        "testEvidenceFiles": evidence_entries,
        "forbiddenPaths": sorted(str(item) for item in policy.get("forbiddenPaths") or []),
        "allowedEntrypoints": sorted(str(item) for item in policy.get("allowedEntrypoints") or []),
        "dependencyLock": {
            "path": "requirements.lock",
            "sha256": sha256_file(dependency_lock),
            "format": "pip-exact-pins-v1",
        },
        "buildEnvironment": {
            "pythonVersion": python_version,
            "pipFreezeHash": pip_freeze_hash,
        },
        "testRunHash": test_run_hash(evidence_entries),
        "verificationContract": {
            "hashAlgorithm": "sha256",
            "canonicalJson": "utf8_sorted_keys_compact",
            "extraRuntimeFileAllowed": False,
            "forbiddenPathAllowed": False,
            "dependencyLockRequired": True,
            "attestedFilesRequired": True,
            "testEvidenceFilesRequired": True,
        },
    }
    canonical = canonical_bytes(manifest)
    manifest["manifestHash"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    manifest["releaseHash"] = "sha256:" + hashlib.sha256(b"release.manifest.v1\0" + canonical).hexdigest()

    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if args.staging_dir:
        copy_staging(
            root,
            Path(args.staging_dir).resolve(),
            runtime_paths,
            attested_paths,
            evidence_copies,
            output,
        )
    print(json.dumps({
        "status": "generated",
        "sourceCommit": manifest["sourceCommit"],
        "releaseHash": manifest["releaseHash"],
        "manifestHash": manifest["manifestHash"],
        "testRunHash": manifest["testRunHash"],
        "dependencyLockHash": manifest["dependencyLock"]["sha256"],
        "pythonVersion": manifest["buildEnvironment"]["pythonVersion"],
        "pipFreezeHash": manifest["buildEnvironment"]["pipFreezeHash"],
        "runtimeFileCount": len(manifest["runtimeFiles"]),
        "attestedFileCount": len(manifest["attestedFiles"]),
        "testEvidenceFileCount": len(manifest["testEvidenceFiles"]),
        "requiredEvidenceFileCount": len(REQUIRED_EVIDENCE_PATHS),
        "evidenceBinding": evidence_binding,
        "output": str(output),
        "stagingDir": args.staging_dir,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
