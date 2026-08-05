from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import re
from importlib import metadata
from pathlib import Path

import pytest

_BOOTSTRAP_PACKAGES = {"pip", "setuptools", "wheel"}
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


def _canonical(manifest: dict) -> bytes:
    payload = {k: v for k, v in manifest.items() if k not in {"manifestHash", "releaseHash"}}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _entry(root: Path, path: Path) -> dict:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    }


def _test_run_hash(entries: list[dict]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item["path"]):
        digest.update(entry["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(entry["sha256"]))
    return "sha256:" + digest.hexdigest()


def _runtime_environment_lines() -> list[str]:
    installed: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            canonical = re.sub(r"[-_.]+", "-", name).lower()
            installed[canonical] = distribution.version
    return [
        f"{name}=={installed[name]}"
        for name in sorted(installed)
        if name not in _BOOTSTRAP_PACKAGES
    ]


def _runtime_environment_hash(lines: list[str] | None = None) -> str:
    materialized = lines if lines is not None else _runtime_environment_lines()
    return "sha256:" + hashlib.sha256(("\n".join(materialized) + "\n").encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _resign_manifest(root: Path) -> dict:
    manifest_path = root / "release" / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in ("runtimeFiles", "attestedFiles", "testEvidenceFiles"):
        refreshed = []
        for entry in manifest[field]:
            refreshed.append(_entry(root, root / entry["path"]))
        manifest[field] = refreshed
    manifest["dependencyLock"]["sha256"] = hashlib.sha256(
        (root / "requirements.lock").read_bytes()
    ).hexdigest()
    manifest["testRunHash"] = _test_run_hash(manifest["testEvidenceFiles"])
    canonical = _canonical(manifest)
    manifest["manifestHash"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    manifest["releaseHash"] = "sha256:" + hashlib.sha256(
        b"release.manifest.v1\0" + canonical
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return manifest


def _write_release(root: Path, *, forbidden: bool = False) -> dict:
    (root / "src").mkdir(parents=True)
    evidence_root = root / "release" / "attestation"
    evidence_root.mkdir(parents=True)
    (root / "tests").mkdir(parents=True)

    runtime = root / "src" / "app.py"
    runtime.write_text("VALUE = 1\n", encoding="utf-8")
    lock = root / "requirements.lock"
    lock.write_text("fastapi==0.139.2\n", encoding="utf-8")
    attested = root / "tests" / "contract.txt"
    attested.write_text("release contract passed\n", encoding="utf-8")

    policy = {
        "schema": "release.policy.v1",
        "productVersion": "22.4.0",
        "runtimeMode": "single_release_sealed_runtime",
        "releasePythonVersion": "3.11.9",
        "runtimeGlobs": ["src/**/*.py", "requirements.lock", "release/release-policy.json"],
        "attestedGlobs": ["tests/**/*"],
        "excludeGlobs": [],
        "allowedEntrypoints": ["src.api.main:app"],
        "forbiddenPaths": ["src/old_runtime.py"],
    }
    policy_path = root / "release" / "release-policy.json"
    policy_path.write_text(
        json.dumps(policy, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )

    environment_lines = _runtime_environment_lines()
    environment_hash = _runtime_environment_hash(environment_lines)
    python_version = platform.python_version()

    for name in ("compile-syntax.log", "static-contract.log", "app-route-smoke.log", "pytest.log"):
        (evidence_root / name).write_text(f"{name}: passed\n", encoding="utf-8")

    _write_json(
        evidence_root / "production-runtime-verification.json",
        {
            "schema": "dependency.lock.verification.v1",
            "verified": True,
            "strictExtras": True,
            "pythonVersion": python_version,
            "pipFreezeHash": environment_hash,
            "missing": [],
            "mismatched": [],
            "extras": [],
        },
    )
    (evidence_root / "attested-files.sha256").write_text(
        f"{hashlib.sha256(attested.read_bytes()).hexdigest()}  tests/contract.txt\n",
        encoding="utf-8",
    )
    attestation = {
        "schema": "release.test-attestation.v1",
        "sourceCommit": "a" * 40,
        "grayPythonVersion": python_version,
        "productionPythonVersion": python_version,
        "requiredEvidenceFiles": list(_REQUIRED_EVIDENCE_PATHS),
    }
    for flag in _REQUIRED_TEST_ATTESTATION_FLAGS:
        attestation[flag] = False if flag == "staticCheckerImportedRuntime" else True
    _write_json(evidence_root / "test-attestation.json", attestation)
    (evidence_root / "pip-freeze.txt").write_text(
        "\n".join(environment_lines) + "\n",
        encoding="utf-8",
    )
    _write_json(
        evidence_root / "python-runtime.json",
        {
            "schema": "release.python-runtime.v1",
            "pythonVersion": python_version,
            "pipFreezeHash": environment_hash,
            "lockedPackageCount": len(environment_lines),
            "installedPackageCount": len(environment_lines),
            "dependencyLockVerified": True,
            "strictExtras": True,
        },
    )

    runtime_files = [_entry(root, path) for path in sorted([runtime, lock, policy_path])]
    attested_files = [_entry(root, attested)]
    evidence_files = [
        _entry(root, path)
        for path in sorted(evidence_root.iterdir(), key=lambda item: item.name)
        if path.is_file()
    ]
    manifest = {
        "schema": "release.manifest.v1",
        "productVersion": "22.4.0",
        "runtimeMode": "single_release_sealed_runtime",
        "sourceCommit": "a" * 40,
        "runtimeFiles": runtime_files,
        "attestedFiles": attested_files,
        "testEvidenceFiles": evidence_files,
        "forbiddenPaths": ["src/old_runtime.py"],
        "allowedEntrypoints": ["src.api.main:app"],
        "dependencyLock": {
            "path": "requirements.lock",
            "sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
            "format": "pip-exact-pins-v1",
        },
        "buildEnvironment": {
            "pythonVersion": python_version,
            "pipFreezeHash": environment_hash,
        },
        "testRunHash": _test_run_hash(evidence_files),
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
    canonical = _canonical(manifest)
    manifest["manifestHash"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    manifest["releaseHash"] = "sha256:" + hashlib.sha256(b"release.manifest.v1\0" + canonical).hexdigest()
    (root / "release" / "release-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    if forbidden:
        (root / "src" / "old_runtime.py").write_text("OLD = True\n", encoding="utf-8")
    return manifest


def test_release_identity_accepts_exact_file_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = _write_release(tmp_path)
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")
    monkeypatch.delenv("AI_RELEASE_MANIFEST", raising=False)

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is True
    assert result["evidenceSemanticVerified"] is True
    assert result["releaseHash"] == manifest["releaseHash"]
    assert result["sourceCommit"] == "a" * 40
    assert result["testRunHash"] == result["calculatedTestRunHash"]
    assert result["runtimeEnvironmentMatch"] is True
    assert result["runtimePythonVersion"] == result["buildPythonVersion"] == "3.11.9"
    assert result["runtimePipFreezeHash"] == result["pipFreezeHash"]
    assert result["verifiedFileCount"] == result["manifestFileCount"] == 3
    assert result["verifiedAttestedFileCount"] == result["attestedFileCount"] == 1
    assert result["verifiedTestEvidenceFileCount"] == result["testEvidenceFileCount"] == len(
        _REQUIRED_EVIDENCE_PATHS
    )
    assert result["extraRuntimeFileCount"] == 0
    assert result["extraAttestedFileCount"] == 0
    assert result["extraTestEvidenceFileCount"] == 0
    assert result["forbiddenPathViolationCount"] == 0


def test_release_identity_rejects_tamper_and_forbidden_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_release(tmp_path, forbidden=True)
    (tmp_path / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is False
    assert any("release_file_hash_mismatch:src/app.py" in item for item in result["errors"])
    assert any("forbidden_release_path_present:src/old_runtime.py" in item for item in result["errors"])


def test_release_identity_rejects_attested_and_evidence_tamper(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_release(tmp_path)
    attested_path = tmp_path / "tests" / "contract.txt"
    original_attested = attested_path.read_text(encoding="utf-8")
    tampered_attested = original_attested.replace("release", "Release", 1)
    assert len(tampered_attested.encode("utf-8")) == len(original_attested.encode("utf-8"))
    attested_path.write_text(tampered_attested, encoding="utf-8")

    attestation_path = tmp_path / "release" / "attestation" / "test-attestation.json"
    original_evidence = attestation_path.read_text(encoding="utf-8")
    tampered_evidence = original_evidence.replace('"sourceCommit":"' + "a" * 40, '"sourceCommit":"' + "b" * 40, 1)
    assert tampered_evidence != original_evidence
    assert len(tampered_evidence.encode("utf-8")) == len(original_evidence.encode("utf-8"))
    attestation_path.write_text(tampered_evidence, encoding="utf-8")

    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is False
    assert "attested_file_hash_mismatch:tests/contract.txt" in result["errors"]
    assert (
        "test_evidence_file_hash_mismatch:release/attestation/test-attestation.json"
        in result["errors"]
    )


def test_release_identity_rejects_self_consistent_cross_commit_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_release(tmp_path)
    attestation_path = tmp_path / "release" / "attestation" / "test-attestation.json"
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    attestation["sourceCommit"] = "b" * 40
    _write_json(attestation_path, attestation)
    manifest = _resign_manifest(tmp_path)
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is False
    assert result["evidenceSemanticVerified"] is False
    assert "test_attestation_source_commit_mismatch" in result["errors"]
    assert result["testRunHash"] == result["calculatedTestRunHash"]

    verifier_path = Path(__file__).resolve().parents[1] / "scripts" / "release_verifier.py"
    spec = importlib.util.spec_from_file_location("v224_root_verifier", verifier_path)
    assert spec and spec.loader
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    root_errors: list[str] = []
    semantic_ok = verifier.verify_evidence_semantics(
        tmp_path,
        manifest,
        manifest["attestedFiles"],
        manifest["testEvidenceFiles"],
        root_errors,
    )
    assert semantic_ok is False
    assert "test_attestation_source_commit_mismatch" in root_errors


def test_release_identity_rejects_self_consistent_attested_digest_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_release(tmp_path)
    digest_path = tmp_path / "release" / "attestation" / "attested-files.sha256"
    digest_path.write_text(f"{'0' * 64}  tests/contract.txt\n", encoding="utf-8")
    _resign_manifest(tmp_path)
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is False
    assert result["evidenceSemanticVerified"] is False
    assert "attested_source_digest_mismatch" in result["errors"]


def test_release_identity_rejects_extra_attested_and_evidence_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_release(tmp_path)
    (tmp_path / "tests" / "legacy_contract_overlay.py").write_text(
        "LEGACY = True\n",
        encoding="utf-8",
    )
    (tmp_path / "release" / "attestation" / "legacy-gray-proof.log").write_text(
        "legacy evidence\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is False
    assert "extra_attested_file:tests/legacy_contract_overlay.py" in result["errors"]
    assert (
        "extra_test_evidence_file:release/attestation/legacy-gray-proof.log"
        in result["errors"]
    )
    assert result["extraAttestedFileCount"] == 1
    assert result["extraTestEvidenceFileCount"] == 1


def test_release_identity_rejects_environment_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_release(tmp_path)
    manifest_path = tmp_path / "release" / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["buildEnvironment"]["pipFreezeHash"] = "sha256:" + "d" * 64
    canonical = _canonical(manifest)
    manifest["manifestHash"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    manifest["releaseHash"] = "sha256:" + hashlib.sha256(b"release.manifest.v1\0" + canonical).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding="utf-8")
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is False
    assert "runtime_pip_freeze_hash_mismatch" in result["errors"]
    assert "production_runtime_environment_hash_mismatch" in result["errors"]


def test_release_identity_rejects_extra_runtime_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _write_release(tmp_path)
    (tmp_path / "src" / "unattested.py").write_text("UNATTESTED = True\n", encoding="utf-8")
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))

    from src.services.release_identity_service import release_identity

    result = release_identity()
    assert result["verified"] is False
    assert "extra_runtime_file:src/unattested.py" in result["errors"]


def test_production_requires_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AI_RELEASE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_RELEASE_REQUIRED", "1")

    from src.services.release_identity_service import assert_release_identity

    with pytest.raises(RuntimeError, match="verified_release_required"):
        assert_release_identity()
