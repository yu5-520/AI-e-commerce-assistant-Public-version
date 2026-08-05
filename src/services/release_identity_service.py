"""V22.4 lightweight content-addressed release identity.

The service verifies that the running directory is the exact file set attested by
GitHub CI. It does not sign or generate a release; generation belongs to the
repository-side release tool and deployment only verifies the resulting bundle.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from copy import deepcopy
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, Iterable

from src.runtime_version import RELEASE_IDENTITY_VERSION, RELEASE_PYTHON_VERSION, VERSION

RELEASE_MANIFEST_SCHEMA = "release.manifest.v1"
RELEASE_RUNTIME_MODE = "single_release_sealed_runtime"
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_BOOTSTRAP_PACKAGES = {"pip", "setuptools", "wheel"}
_EXPECTED_VERIFICATION_CONTRACT = {
    "hashAlgorithm": "sha256",
    "canonicalJson": "utf8_sorted_keys_compact",
    "extraRuntimeFileAllowed": False,
    "forbiddenPathAllowed": False,
    "dependencyLockRequired": True,
    "attestedFilesRequired": True,
    "testEvidenceFilesRequired": True,
}
_REQUIRED_EVIDENCE_PATHS = (
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
_REQUIRED_TEST_ATTESTATION_FLAGS = (
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
_VERIFIED_CACHE: dict[tuple[str, str, bool, int, int], Dict[str, Any]] = {}


def project_root() -> Path:
    configured = str(os.getenv("AI_RELEASE_ROOT") or "").strip()
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[2]


def manifest_path(root: Path | None = None) -> Path:
    configured = str(os.getenv("AI_RELEASE_MANIFEST") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (root or project_root()) / "release" / "release-manifest.json"


def _canonical_bytes(manifest: Dict[str, Any]) -> bytes:
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in {"manifestHash", "releaseHash"}
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def expected_manifest_hash(manifest: Dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(manifest)).hexdigest()


def expected_release_hash(manifest: Dict[str, Any]) -> str:
    digest = hashlib.sha256(
        RELEASE_MANIFEST_SCHEMA.encode("utf-8") + b"\0" + _canonical_bytes(manifest)
    ).hexdigest()
    return "sha256:" + digest


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def runtime_pip_freeze_hash() -> str:
    installed: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            installed[_canonical_package_name(name)] = distribution.version
    lines = [
        f"{name}=={installed[name]}"
        for name in sorted(installed)
        if name not in _BOOTSTRAP_PACKAGES
    ]
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _safe_path(root: Path, raw: str) -> Path:
    relative = Path(str(raw or ""))
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe_release_path:{raw}")
    resolved = (root / relative).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError(f"release_path_escapes_root:{raw}")
    return resolved


def _read_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release_manifest_must_be_object")
    return payload


def _read_evidence_json(path: Path, label: str, errors: list[str]) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"{label}_read_failed:{exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append(f"{label}_must_be_object")
        return {}
    return payload


def _policy_paths(root: Path, policy: Dict[str, Any], field: str) -> set[str]:
    paths: set[str] = set()
    for pattern in policy.get(field) or []:
        for path in root.glob(str(pattern)):
            if path.is_file():
                paths.add(path.relative_to(root).as_posix())
    for pattern in policy.get("excludeGlobs") or []:
        for path in root.glob(str(pattern)):
            if path.is_file():
                paths.discard(path.relative_to(root).as_posix())
    paths.discard("release/release-manifest.json")
    return paths


def _evidence_paths(root: Path) -> set[str]:
    evidence_root = root / "release" / "attestation"
    if not evidence_root.is_dir():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in evidence_root.rglob("*")
        if path.is_file()
    }


def _validate_file_entries(
    root: Path,
    entries: Iterable[Any],
    *,
    category: str,
    global_seen: set[str],
    verify_content: bool,
) -> tuple[list[str], int, set[str]]:
    errors: list[str] = []
    verified = 0
    seen: set[str] = set()
    materialized = list(entries) if isinstance(entries, list) else []
    if not isinstance(entries, list):
        errors.append(f"{category}_files_must_be_list")
        return errors, verified, seen
    if not materialized:
        errors.append(f"{category}_files_empty")
        return errors, verified, seen
    for entry in materialized:
        if not isinstance(entry, dict):
            errors.append(f"{category}_file_entry_must_be_object")
            continue
        raw_path = str(entry.get("path") or "")
        if raw_path in global_seen:
            errors.append(f"duplicate_release_file:{raw_path}")
            continue
        global_seen.add(raw_path)
        seen.add(raw_path)
        expected = str(entry.get("sha256") or "")
        expected_size = entry.get("size")
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            errors.append(f"invalid_{category}_file_hash:{raw_path}")
            continue
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            errors.append(f"invalid_{category}_file_size:{raw_path}")
            continue
        try:
            path = _safe_path(root, raw_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path.is_symlink():
            errors.append(f"{category}_file_symlink_forbidden:{raw_path}")
            continue
        if not path.is_file():
            errors.append(f"{category}_file_missing:{raw_path}")
            continue
        if path.stat().st_size != expected_size:
            errors.append(f"{category}_file_size_mismatch:{raw_path}")
            continue
        if verify_content and file_sha256(path) != expected:
            errors.append(f"{category}_file_hash_mismatch:{raw_path}")
            continue
        verified += 1
    return errors, verified, seen


def _expected_test_run_hash(entries: Iterable[Any]) -> str | None:
    materialized = list(entries) if isinstance(entries, list) else []
    if not materialized:
        return None
    digest = hashlib.sha256()
    for entry in sorted(
        materialized,
        key=lambda item: str(item.get("path") or "") if isinstance(item, dict) else "",
    ):
        if not isinstance(entry, dict):
            return None
        path = str(entry.get("path") or "")
        file_hash = str(entry.get("sha256") or "")
        if not path or not re.fullmatch(r"[0-9a-f]{64}", file_hash):
            return None
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
    return "sha256:" + digest.hexdigest()


def _expected_attested_digest(entries: Iterable[Any]) -> str | None:
    materialized = list(entries) if isinstance(entries, list) else []
    if not materialized:
        return None
    lines: list[str] = []
    for entry in sorted(
        materialized,
        key=lambda item: str(item.get("path") or "") if isinstance(item, dict) else "",
    ):
        if not isinstance(entry, dict):
            return None
        path = str(entry.get("path") or "")
        file_hash = str(entry.get("sha256") or "")
        if not path or not re.fullmatch(r"[0-9a-f]{64}", file_hash):
            return None
        lines.append(f"{file_hash}  {path}")
    return "\n".join(lines) + "\n"


def _verify_evidence_semantics(
    root: Path,
    manifest: Dict[str, Any],
    attested_entries: Iterable[Any],
    evidence_entries: Iterable[Any],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    evidence_root = root / "release" / "attestation"
    entry_map = {
        str(entry.get("path") or ""): entry
        for entry in evidence_entries
        if isinstance(entry, dict)
    } if isinstance(evidence_entries, list) else {}

    for relative in _REQUIRED_EVIDENCE_PATHS:
        manifest_evidence_path = f"release/attestation/{relative}"
        entry = entry_map.get(manifest_evidence_path)
        if entry is None:
            errors.append(f"required_test_evidence_not_manifested:{relative}")
            continue
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            errors.append(f"required_test_evidence_empty:{relative}")

    source_commit = str(manifest.get("sourceCommit") or "")
    build_environment = manifest.get("buildEnvironment") or {}
    python_version = str(build_environment.get("pythonVersion") or "")
    pip_freeze_hash = str(build_environment.get("pipFreezeHash") or "")

    test_attestation = _read_evidence_json(
        evidence_root / "test-attestation.json", "test_attestation", errors
    )
    if test_attestation.get("schema") != "release.test-attestation.v1":
        errors.append("test_attestation_schema_mismatch")
    if test_attestation.get("sourceCommit") != source_commit:
        errors.append("test_attestation_source_commit_mismatch")
    if test_attestation.get("grayPythonVersion") != python_version:
        errors.append("test_attestation_gray_python_mismatch")
    if test_attestation.get("productionPythonVersion") != python_version:
        errors.append("test_attestation_production_python_mismatch")
    if sorted(str(item) for item in test_attestation.get("requiredEvidenceFiles") or []) != sorted(
        _REQUIRED_EVIDENCE_PATHS
    ):
        errors.append("test_attestation_required_evidence_mismatch")
    for flag in _REQUIRED_TEST_ATTESTATION_FLAGS:
        expected = False if flag == "staticCheckerImportedRuntime" else True
        if test_attestation.get(flag) is not expected:
            errors.append(f"test_attestation_flag_mismatch:{flag}")

    production_runtime = _read_evidence_json(
        evidence_root / "production-runtime-verification.json",
        "production_runtime_verification",
        errors,
    )
    if production_runtime.get("schema") != "dependency.lock.verification.v1":
        errors.append("production_runtime_schema_mismatch")
    if production_runtime.get("verified") is not True:
        errors.append("production_runtime_not_verified")
    if production_runtime.get("strictExtras") is not True:
        errors.append("production_runtime_not_strict")
    if production_runtime.get("pythonVersion") != python_version:
        errors.append("production_runtime_python_mismatch")
    if production_runtime.get("pipFreezeHash") != pip_freeze_hash:
        errors.append("production_runtime_environment_hash_mismatch")
    for field in ("missing", "mismatched", "extras"):
        if production_runtime.get(field) != []:
            errors.append(f"production_runtime_{field}_not_empty")

    python_runtime = _read_evidence_json(
        evidence_root / "python-runtime.json", "python_runtime", errors
    )
    if python_runtime.get("schema") != "release.python-runtime.v1":
        errors.append("python_runtime_schema_mismatch")
    if python_runtime.get("pythonVersion") != python_version:
        errors.append("python_runtime_version_mismatch")
    if python_runtime.get("pipFreezeHash") != pip_freeze_hash:
        errors.append("python_runtime_environment_hash_mismatch")
    if python_runtime.get("dependencyLockVerified") is not True:
        errors.append("python_runtime_dependency_lock_not_verified")
    if python_runtime.get("strictExtras") is not True:
        errors.append("python_runtime_not_strict")

    freeze_path = evidence_root / "pip-freeze.txt"
    if freeze_path.is_file() and "sha256:" + file_sha256(freeze_path) != pip_freeze_hash:
        errors.append("pip_freeze_evidence_hash_mismatch")

    expected_digest = _expected_attested_digest(attested_entries)
    digest_path = evidence_root / "attested-files.sha256"
    if expected_digest is None:
        errors.append("attested_source_digest_invalid")
    elif digest_path.is_file() and digest_path.read_text(encoding="utf-8") != expected_digest:
        errors.append("attested_source_digest_mismatch")

    return not errors, errors


def _cache_key(root: Path, path: Path, required: bool) -> tuple[str, str, bool, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(root), str(path), required, int(stat.st_mtime_ns), int(stat.st_size))


def release_identity(*, verify_content: bool = True) -> Dict[str, Any]:
    root = project_root()
    path = manifest_path(root)
    required = str(os.getenv("AI_RELEASE_REQUIRED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    runtime_python_version = platform.python_version()
    cache_key = _cache_key(root, path, required)
    if not verify_content and cache_key is not None:
        cached = _VERIFIED_CACHE.get(cache_key)
        if cached is not None:
            return deepcopy(cached)

    base: Dict[str, Any] = {
        "schema": "release.identity.v1",
        "version": RELEASE_IDENTITY_VERSION,
        "productVersion": VERSION,
        "runtimeMode": RELEASE_RUNTIME_MODE,
        "runtimeRoot": str(root),
        "manifestPath": str(path),
        "required": required,
        "manifestPresent": path.is_file(),
        "verified": False,
        "status": "unsealed",
        "sourceCommit": None,
        "releaseHash": None,
        "manifestHash": None,
        "testRunHash": None,
        "calculatedTestRunHash": None,
        "evidenceSemanticVerified": False,
        "dependencyLockHash": None,
        "buildPythonVersion": None,
        "runtimePythonVersion": runtime_python_version,
        "runtimePythonCompatible": runtime_python_version == RELEASE_PYTHON_VERSION,
        "pipFreezeHash": None,
        "runtimePipFreezeHash": None,
        "runtimeEnvironmentMatch": False,
        "verifiedFileCount": 0,
        "manifestFileCount": 0,
        "verifiedAttestedFileCount": 0,
        "attestedFileCount": 0,
        "verifiedTestEvidenceFileCount": 0,
        "testEvidenceFileCount": 0,
        "forbiddenPathViolationCount": 0,
        "extraRuntimeFileCount": 0,
        "extraAttestedFileCount": 0,
        "extraTestEvidenceFileCount": 0,
        "manifestRuntimeFileOutsidePolicyCount": 0,
        "manifestAttestedFileOutsidePolicyCount": 0,
        "manifestTestEvidenceFileMissingCount": 0,
        "errors": [],
    }
    if not path.is_file():
        if required:
            base["status"] = "manifest_missing"
            base["errors"] = ["release_manifest_missing"]
        return base

    errors: list[str] = []
    try:
        manifest = _read_json(path)
    except Exception as exc:
        base["status"] = "manifest_invalid"
        base["errors"] = [f"release_manifest_read_failed:{exc}"]
        return base

    policy_path = root / "release" / "release-policy.json"
    if policy_path.is_file():
        try:
            policy = _read_json(policy_path)
        except Exception as exc:
            policy = {}
            errors.append(f"release_policy_read_failed:{exc}")
    else:
        policy = {}
        errors.append("release_policy_missing")

    source_commit = str(manifest.get("sourceCommit") or "")
    manifest_hash = str(manifest.get("manifestHash") or "")
    release_hash = str(manifest.get("releaseHash") or "")
    test_run_hash = str(manifest.get("testRunHash") or "")
    runtime_entries = manifest.get("runtimeFiles") or []
    attested_entries = manifest.get("attestedFiles") or []
    evidence_entries = manifest.get("testEvidenceFiles") or []
    dependency_lock = manifest.get("dependencyLock") or {}
    dependency_lock_path = str(dependency_lock.get("path") or "")
    dependency_lock_hash = str(dependency_lock.get("sha256") or "")
    build_environment = manifest.get("buildEnvironment") or {}
    build_python_version = str(build_environment.get("pythonVersion") or "")
    pip_freeze_hash = str(build_environment.get("pipFreezeHash") or "")
    actual_runtime_pip_freeze_hash = runtime_pip_freeze_hash()

    if manifest.get("schema") != RELEASE_MANIFEST_SCHEMA:
        errors.append("release_manifest_schema_mismatch")
    if manifest.get("productVersion") != VERSION:
        errors.append("release_product_version_mismatch")
    if manifest.get("runtimeMode") != RELEASE_RUNTIME_MODE:
        errors.append("release_runtime_mode_mismatch")
    if policy.get("productVersion") != manifest.get("productVersion"):
        errors.append("release_policy_product_version_mismatch")
    if policy.get("runtimeMode") != manifest.get("runtimeMode"):
        errors.append("release_policy_runtime_mode_mismatch")
    if policy.get("releasePythonVersion") != RELEASE_PYTHON_VERSION:
        errors.append("release_policy_python_version_mismatch")
    if sorted(str(item) for item in policy.get("forbiddenPaths") or []) != sorted(
        str(item) for item in manifest.get("forbiddenPaths") or []
    ):
        errors.append("release_policy_forbidden_paths_mismatch")
    if sorted(str(item) for item in policy.get("allowedEntrypoints") or []) != sorted(
        str(item) for item in manifest.get("allowedEntrypoints") or []
    ):
        errors.append("release_policy_entrypoints_mismatch")
    if manifest.get("verificationContract") != _EXPECTED_VERIFICATION_CONTRACT:
        errors.append("release_verification_contract_mismatch")
    if not _COMMIT_RE.fullmatch(source_commit):
        errors.append("release_source_commit_invalid")
    if not _HASH_RE.fullmatch(test_run_hash):
        errors.append("release_test_run_hash_invalid")
    if manifest_hash != expected_manifest_hash(manifest):
        errors.append("release_manifest_hash_mismatch")
    if release_hash != expected_release_hash(manifest):
        errors.append("release_root_hash_mismatch")

    if dependency_lock_path != "requirements.lock":
        errors.append("release_dependency_lock_path_invalid")
    elif not re.fullmatch(r"[0-9a-f]{64}", dependency_lock_hash):
        errors.append("release_dependency_lock_hash_invalid")
    else:
        lock_path = _safe_path(root, dependency_lock_path)
        if lock_path.is_symlink():
            errors.append("release_dependency_lock_symlink_forbidden")
        elif not lock_path.is_file():
            errors.append("release_dependency_lock_missing")
        elif verify_content and file_sha256(lock_path) != dependency_lock_hash:
            errors.append("release_dependency_lock_hash_mismatch")
    if dependency_lock.get("format") != "pip-exact-pins-v1":
        errors.append("release_dependency_lock_format_invalid")
    if build_python_version != RELEASE_PYTHON_VERSION:
        errors.append("release_python_version_invalid")
    if not _HASH_RE.fullmatch(pip_freeze_hash):
        errors.append("release_pip_freeze_hash_invalid")
    if runtime_python_version != build_python_version:
        errors.append("runtime_python_build_version_mismatch")
    if runtime_python_version != RELEASE_PYTHON_VERSION:
        errors.append("runtime_python_release_version_mismatch")
    if actual_runtime_pip_freeze_hash != pip_freeze_hash:
        errors.append("runtime_pip_freeze_hash_mismatch")

    global_seen: set[str] = set()
    runtime_errors, runtime_verified, runtime_seen = _validate_file_entries(
        root,
        runtime_entries,
        category="release",
        global_seen=global_seen,
        verify_content=verify_content,
    )
    attested_errors, attested_verified, attested_seen = _validate_file_entries(
        root,
        attested_entries,
        category="attested",
        global_seen=global_seen,
        verify_content=verify_content,
    )
    evidence_errors, evidence_verified, evidence_seen = _validate_file_entries(
        root,
        evidence_entries,
        category="test_evidence",
        global_seen=global_seen,
        verify_content=verify_content,
    )
    errors.extend(runtime_errors)
    errors.extend(attested_errors)
    errors.extend(evidence_errors)

    calculated_test_run_hash = _expected_test_run_hash(evidence_entries)
    if calculated_test_run_hash is None:
        errors.append("release_test_evidence_invalid")
    elif test_run_hash != calculated_test_run_hash:
        errors.append("release_test_run_hash_mismatch")

    evidence_semantic_verified, evidence_semantic_errors = _verify_evidence_semantics(
        root,
        manifest,
        attested_entries,
        evidence_entries,
    )
    errors.extend(evidence_semantic_errors)

    forbidden_violations: list[str] = []
    for raw in manifest.get("forbiddenPaths") or []:
        try:
            candidate = _safe_path(root, str(raw))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if candidate.exists() or candidate.is_symlink():
            forbidden_violations.append(str(raw))
            errors.append(f"forbidden_release_path_present:{raw}")

    actual_runtime = _policy_paths(root, policy, "runtimeGlobs")
    extra_runtime = sorted(actual_runtime - runtime_seen)
    missing_runtime_policy = sorted(runtime_seen - actual_runtime)
    errors.extend(f"extra_runtime_file:{item}" for item in extra_runtime)
    errors.extend(f"manifest_runtime_file_outside_policy:{item}" for item in missing_runtime_policy)

    actual_attested = _policy_paths(root, policy, "attestedGlobs")
    extra_attested = sorted(actual_attested - attested_seen)
    missing_attested_policy = sorted(attested_seen - actual_attested)
    errors.extend(f"extra_attested_file:{item}" for item in extra_attested)
    errors.extend(f"manifest_attested_file_outside_policy:{item}" for item in missing_attested_policy)

    actual_evidence = _evidence_paths(root)
    extra_evidence = sorted(actual_evidence - evidence_seen)
    missing_evidence = sorted(evidence_seen - actual_evidence)
    errors.extend(f"extra_test_evidence_file:{item}" for item in extra_evidence)
    errors.extend(f"manifest_test_evidence_file_missing:{item}" for item in missing_evidence)

    environment_match = (
        runtime_python_version == build_python_version == RELEASE_PYTHON_VERSION
        and actual_runtime_pip_freeze_hash == pip_freeze_hash
    )
    base.update(
        sourceCommit=source_commit or None,
        releaseHash=release_hash or None,
        manifestHash=manifest_hash or None,
        testRunHash=test_run_hash or None,
        calculatedTestRunHash=calculated_test_run_hash,
        evidenceSemanticVerified=evidence_semantic_verified,
        dependencyLockHash=dependency_lock_hash or None,
        buildPythonVersion=build_python_version or None,
        runtimePythonVersion=runtime_python_version,
        runtimePythonCompatible=(
            runtime_python_version == build_python_version == RELEASE_PYTHON_VERSION
        ),
        pipFreezeHash=pip_freeze_hash or None,
        runtimePipFreezeHash=actual_runtime_pip_freeze_hash,
        runtimeEnvironmentMatch=environment_match,
        verifiedFileCount=runtime_verified,
        manifestFileCount=len(runtime_entries) if isinstance(runtime_entries, list) else 0,
        verifiedAttestedFileCount=attested_verified,
        attestedFileCount=len(attested_entries) if isinstance(attested_entries, list) else 0,
        verifiedTestEvidenceFileCount=evidence_verified,
        testEvidenceFileCount=len(evidence_entries) if isinstance(evidence_entries, list) else 0,
        forbiddenPathViolationCount=len(forbidden_violations),
        forbiddenPathViolations=forbidden_violations,
        extraRuntimeFileCount=len(extra_runtime),
        extraRuntimeFiles=extra_runtime,
        extraAttestedFileCount=len(extra_attested),
        extraAttestedFiles=extra_attested,
        extraTestEvidenceFileCount=len(extra_evidence),
        extraTestEvidenceFiles=extra_evidence,
        manifestRuntimeFileOutsidePolicyCount=len(missing_runtime_policy),
        manifestRuntimeFilesOutsidePolicy=missing_runtime_policy,
        manifestAttestedFileOutsidePolicyCount=len(missing_attested_policy),
        manifestAttestedFilesOutsidePolicy=missing_attested_policy,
        manifestTestEvidenceFileMissingCount=len(missing_evidence),
        manifestTestEvidenceFilesMissing=missing_evidence,
        errors=errors,
        verified=not errors,
        status="verified" if not errors else "verification_failed",
    )
    if base["verified"] and cache_key is not None:
        _VERIFIED_CACHE.clear()
        _VERIFIED_CACHE[cache_key] = deepcopy(base)
    return base


def assert_release_identity(*, required: bool | None = None) -> Dict[str, Any]:
    identity = release_identity(verify_content=True)
    effective_required = identity["required"] if required is None else bool(required)
    if identity["manifestPresent"] and not identity["verified"]:
        raise RuntimeError("release_identity_verification_failed:" + ";".join(identity["errors"]))
    if effective_required and not identity["verified"]:
        raise RuntimeError("verified_release_required:" + ";".join(identity["errors"] or [identity["status"]]))
    return identity


__all__ = [
    "RELEASE_MANIFEST_SCHEMA",
    "RELEASE_RUNTIME_MODE",
    "assert_release_identity",
    "expected_manifest_hash",
    "expected_release_hash",
    "file_sha256",
    "manifest_path",
    "project_root",
    "release_identity",
    "runtime_pip_freeze_hash",
]
