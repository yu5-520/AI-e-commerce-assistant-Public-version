#!/usr/bin/env python3
"""Pure-static fail-closed checker for V22.4 Release Hash Seal Lite.

This checker must never import src.*, FastAPI, database modules, Agent modules or
Worker modules. Runtime behavior is tested separately in a clean subprocess.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def parsed(path: str) -> ast.AST:
    return ast.parse(text(path), filename=path)


def literal_assignments(path: str) -> dict[str, Any]:
    values: dict[str, Any] = {}

    def resolve(node: ast.AST) -> Any:
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        return ast.literal_eval(node)

    tree = parsed(path)
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        try:
            value = resolve(node.value)
        except Exception:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    return values


def _resolve_static_path_expression(
    node: ast.AST,
    values: dict[str, PurePosixPath],
) -> PurePosixPath:
    if isinstance(node, ast.Name) and node.id in values:
        return values[node.id]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return PurePosixPath(node.value)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"Path", "PurePath", "PurePosixPath"}
        and len(node.args) == 1
        and not node.keywords
    ):
        return _resolve_static_path_expression(node.args[0], values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _resolve_static_path_expression(node.left, values)
        right = _resolve_static_path_expression(node.right, values)
        if right.is_absolute():
            raise ValueError("absolute_static_path_suffix")
        return left / right
    raise ValueError(f"unsupported_static_path_expression:{ast.dump(node, include_attributes=False)}")


def static_path_assignments(path: str) -> dict[str, str]:
    values: dict[str, PurePosixPath] = {}
    tree = parsed(path)
    for node in getattr(tree, "body", []):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        try:
            value = _resolve_static_path_expression(node.value, values)
        except ValueError:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    return {name: value.as_posix() for name, value in values.items()}


def assert_no_runtime_imports() -> None:
    tree = parsed("scripts/check_release_contract.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            assert not module.startswith("src"), f"static_checker_imports_runtime:{module}"
            assert not module.startswith("fastapi"), f"static_checker_imports_fastapi:{module}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                name = str(alias.name or "")
                assert not name.startswith("src"), f"static_checker_imports_runtime:{name}"
                assert not name.startswith("fastapi"), f"static_checker_imports_fastapi:{name}"


def assert_route_contract() -> None:
    system_source = text("src/api/routes/system.py")
    main_source = text("src/api/main.py")
    assert 'APIRouter(prefix="/api/system"' in system_source
    assert '@router.get("/release-identity")' in system_source
    assert "def release_identity_view" in system_source
    assert re.search(r"\bfrom src\.api\.routes import \(", main_source)
    assert re.search(r"\bsystem,", main_source)
    assert "app.include_router(route_module.router)" in main_source
    assert 'from src.services.release_identity_service import assert_release_identity, release_identity' in main_source
    assert "assert_release_identity()" in main_source
    assert '@app.get("/api/version")' in main_source


def assert_exact_lock(path: str) -> None:
    source = text(path)
    pins = []
    for line_number, raw in enumerate(source.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        assert re.fullmatch(r"[A-Za-z0-9_.-]+==[^\s;]+", line), (
            f"non_exact_dependency:{path}:{line_number}:{line}"
        )
        pins.append(line)
    assert pins, f"empty_dependency_lock:{path}"


def main() -> None:
    assert_no_runtime_imports()

    versions = literal_assignments("src/runtime_version.py")
    assert versions["VERSION"] == "22.4.0"
    assert versions["RUNTIME_MODE"] == "single_release_sealed_runtime"
    assert versions["RELEASE_MANIFEST_VERSION"] == "release.manifest.v1"
    assert versions["RELEASE_PYTHON_VERSION"] == "3.11.9"
    assert versions["AGENT_RUNTIME_HARD_INTERFACE_VERSION"] == "22.3.0"
    assert versions["STATION_AGENT_WORKER_VERSION"] == "22.4.0"

    required = (
        "release/release-policy.json",
        "release/release-manifest.schema.json",
        "requirements.lock",
        "requirements-dev.lock",
        "src/services/release_identity_service.py",
        "scripts/check_dependency_lock.py",
        "scripts/check_python36_bootstrap.py",
        "scripts/generate_release_manifest.py",
        "scripts/release_verifier.py",
        "scripts/install_release_verifier.sh",
        "scripts/deploy_release.sh",
        "scripts/runtime_exclusivity_guard.sh",
        "scripts/sqlite_backup_rotate.py",
        ".github/workflows/release-hash-seal.yml",
        "docs/V22.4.0_RELEASE_HASH_SEAL.md",
        "tests/test_v22_4_release_hash_seal.py",
        "tests/test_v22_4_app_route_smoke.py",
        "tests/test_v22_4_static_path_contract.py",
        "java-control-plane/src/main/java/com/zcentury/v24/ProductionAuthorityMain.java",
        "governance/v24/production-authority-bundle-v24.json",
        "governance/v24/authority-generation-policy-v24.json",
        "scripts/build_v24_production_bundle.sh",
        "scripts/verify_v24_authority_generation.sh",
        "scripts/verify_v24_production_bundle.sh",
        "scripts/start_v24_authority.sh",
        ".github/workflows/v24-production-authority-bundle.yml",
    )
    for path in required:
        assert (ROOT / path).is_file(), path

    removed = (
        "scripts/deploy_v22.sh",
        "scripts/deploy_fast.sh",
        "scripts/deploy_atomic.sh",
        "scripts/check_v22_contract.py",
        "scripts/check_v16_manifest.py",
        "scripts/verify_release_manifest.py",
        ".github/workflows/v22-single-runtime.yml",
        "docs/V22.3.0.1_INTERFACE_CLOSURE.md",
        "MVP_V16_FILE_MANIFEST.md",
        "config/v16_mvp_file_manifest.json",
        "src/services/end_to_end_agent_flow_v226_hardening_service.py",
    )
    for path in removed:
        assert not (ROOT / path).exists(), path

    assert_exact_lock("requirements.lock")
    assert_exact_lock("requirements-dev.lock")
    assert text("requirements.txt").strip().endswith("-r requirements.lock")

    policy = json.loads(text("release/release-policy.json"))
    runtime_globs = set(policy.get("runtimeGlobs") or [])
    attested_globs = set(policy.get("attestedGlobs") or [])
    forbidden_paths = set(policy.get("forbiddenPaths") or [])
    rules = policy.get("rules") or {}
    assert policy["releasePythonVersion"] == "3.11.9"
    for runtime_path in (
        "src/**/*",
        "web_demo/**/*",
        "config/**/*",
        "requirements.txt",
        "requirements.lock",
        "scripts/deploy_release.sh",
        "scripts/release_verifier.py",
        "scripts/install_release_verifier.sh",
        "scripts/check_dependency_lock.py",
        "scripts/runtime_exclusivity_guard.sh",
        "scripts/sqlite_backup_rotate.py",
        "runtime/java/**/*",
        "scripts/start_v24_authority.sh",
    ):
        assert runtime_path in runtime_globs, runtime_path
    assert "requirements-dev.lock" in attested_globs
    assert "tests/**/*" in attested_globs
    assert "scripts/check_python36_bootstrap.py" in attested_globs
    for java_attested_path in (
        "java-control-plane/src/main/java/**/*",
        "governance/v24/production-authority-bundle-v24.json",
        "governance/v24/authority-generation-policy-v24.json",
        "scripts/build_v24_production_bundle.sh",
        "scripts/verify_v24_authority_generation.sh",
        "scripts/verify_v24_production_bundle.sh",
        ".github/workflows/v24-production-authority-bundle.yml",
    ):
        assert java_attested_path in attested_globs, java_attested_path
    for path in removed:
        assert path in forbidden_paths, path
    assert rules["deployCurrentBranchDirectly"] is False
    assert rules["extraRuntimeFileAllowed"] is False
    assert rules["deploymentControllerMustComeFromReleaseBundle"] is True
    assert rules["releaseBundleMustBeOutsideDeploymentRoot"] is True
    assert rules["dependencyLockRequired"] is True
    assert rules["runtimeDependencyDriftAllowed"] is False
    assert rules["attestedFilesMustShipInBundle"] is True
    assert rules["testEvidenceFilesMustShipInBundle"] is True
    assert rules["testRunHashMustBeRecomputed"] is True
    assert rules["exactPythonPatchRequired"] is True
    assert rules["python36BootstrapCompatibilityRequired"] is True
    assert rules["rootVerifierOrdinaryRotationAllowed"] is False
    assert rules["rootVerifierExplicitOldHashRequiredForRotation"] is True
    assert rules["validatedSqliteBackupRequiredBeforeSwitch"] is True
    assert rules["sharedStateMigrationRequiredOnFirstSeal"] is True
    assert rules["singleActiveRepositoryServiceRequired"] is True
    assert rules["strayRepositoryProcessAllowed"] is False
    assert rules["legacyMutableWorkingTreeAllowedAfterSuccess"] is False

    schema = json.loads(text("release/release-manifest.schema.json"))
    schema_required = set(schema.get("required") or [])
    for required_field in (
        "dependencyLock",
        "buildEnvironment",
        "attestedFiles",
        "testEvidenceFiles",
        "verificationContract",
    ):
        assert required_field in schema_required, required_field
    build_properties = ((schema.get("properties") or {}).get("buildEnvironment") or {}).get("properties") or {}
    assert build_properties["pythonVersion"]["const"] == "3.11.9"
    contract_required = set(
        (((schema.get("properties") or {}).get("verificationContract") or {}).get("required") or [])
    )
    assert "attestedFilesRequired" in contract_required
    assert "testEvidenceFilesRequired" in contract_required
    for field in ("runtimeFiles", "attestedFiles", "testEvidenceFiles"):
        assert ((schema.get("properties") or {}).get(field) or {}).get("uniqueItems") is True

    generator_path = "scripts/generate_release_manifest.py"
    generator = text(generator_path)
    generator_paths = static_path_assignments(generator_path)
    assert generator_paths.get("EVIDENCE_PREFIX") == "release/attestation", (
        "manifest_evidence_prefix_mismatch:"
        f"{generator_paths.get('EVIDENCE_PREFIX')}"
    )
    for marker in (
        "REQUIRED_EVIDENCE_PATHS",
        "REQUIRED_TEST_ATTESTATION_FLAGS",
        "compile-syntax.log",
        "static-contract.log",
        "app-route-smoke.log",
        "pytest.log",
        "production-runtime-verification.json",
        "attested-files.sha256",
        "test-attestation.json",
        "pip-freeze.txt",
        "python-runtime.json",
        "require_evidence_floor",
        "release gray-test evidence floor is incomplete",
        "validate_evidence_semantics",
        "test_attestation_source_commit_mismatch",
        "production_runtime_environment_hash_mismatch",
        "attested_source_digest_mismatch",
        "testEvidenceFiles",
        "attested_paths",
        "evidence_copies",
        "test evidence entries are required",
        "releasePythonVersion",
        "--runtime-python",
        "runtime dependency verification failed",
    ):
        assert marker in generator, marker
    assert 'parser.add_argument("--test-evidence-dir", required=True)' in generator
    assert 'parser.add_argument("--runtime-python", required=True)' in generator

    verifier = text("scripts/release_verifier.py")
    for marker in (
        'PYTHON_VERSION = "3.11.9"',
        "REQUIRED_EVIDENCE_PATHS",
        "REQUIRED_TEST_ATTESTATION_FLAGS",
        "verify_evidence_semantics",
        "evidenceSemanticVerified",
        "test_attestation_source_commit_mismatch",
        "production_runtime_environment_hash_mismatch",
        "attested_source_digest_mismatch",
        "release_root_hash_mismatch",
        "file_hash_mismatch",
        'root, runtime_entries, "release"',
        'root, attested_entries, "attested"',
        'root, evidence_entries, "test_evidence"',
        "release_test_run_hash_mismatch",
        "release_dependency_lock_hash_mismatch",
        "release_policy_python_version_mismatch",
        "release_verification_contract_mismatch",
        "extra_attested_file",
        "extra_test_evidence_file",
        "forbidden_release_path_present",
        "extra_runtime_file",
    ):
        assert marker in verifier, marker
    assert "from __future__ import annotations" not in verifier
    assert "list[" not in verifier
    assert "set[" not in verifier

    python36_checker = text("scripts/check_python36_bootstrap.py")
    for marker in (
        "feature_version=PY36",
        "scripts/release_verifier.py",
        "runtime_exclusivity_guard.sh",
        "python36_unsupported_bootstrap_fragments",
        ".unlink(missing_ok=",
        "capture_output=",
    ):
        assert marker in python36_checker, marker

    installer = text("scripts/install_release_verifier.sh")
    for marker in (
        "AI_RELEASE_VERIFIER_ROTATE",
        "AI_RELEASE_VERIFIER_EXPECTED_OLD_SHA256",
        "target and hash record must exist together",
        "ordinary release deployment cannot rotate root trust",
        "Expected old verifier SHA256 does not match",
        "Verifier changed during installation",
        "COMMIT_STARTED",
        "Installed verifier post-commit SHA256 mismatch",
        "mktemp",
        "Pinned verifier SHA256",
    ):
        assert marker in installer, marker
    assert "install -o root -g root -m 0755" in installer
    assert "mv -f \"$TEMP_TARGET\" \"$TARGET\"" in installer

    identity = text("src/services/release_identity_service.py")
    for marker in (
        "RELEASE_PYTHON_VERSION",
        "_REQUIRED_EVIDENCE_PATHS",
        "_REQUIRED_TEST_ATTESTATION_FLAGS",
        "_verify_evidence_semantics",
        "evidenceSemanticVerified",
        "test_attestation_source_commit_mismatch",
        "production_runtime_environment_hash_mismatch",
        "attested_source_digest_mismatch",
        "verifiedAttestedFileCount",
        "verifiedTestEvidenceFileCount",
        "calculatedTestRunHash",
        "runtimePythonVersion",
        "runtimePipFreezeHash",
        "runtimeEnvironmentMatch",
        "runtime_python_build_version_mismatch",
        "runtime_pip_freeze_hash_mismatch",
        "release_verification_contract_mismatch",
        "extraAttestedFileCount",
        "extraTestEvidenceFileCount",
        "extra_attested_file",
        "extra_test_evidence_file",
    ):
        assert marker in identity, marker

    release_tests = text("tests/test_v22_4_release_hash_seal.py")
    for marker in (
        "_resign_manifest",
        "test_release_identity_rejects_self_consistent_cross_commit_evidence",
        "test_release_identity_rejects_self_consistent_attested_digest_mismatch",
        "test_attestation_source_commit_mismatch",
        "attested_source_digest_mismatch",
        "evidenceSemanticVerified",
    ):
        assert marker in release_tests, marker

    dependency_checker = text("scripts/check_dependency_lock.py")
    for marker in (
        "non_exact_lock_entry",
        "mismatched",
        "strictExtras",
        "pipFreezeHash",
        "canonicalEnvironmentLines",
        "--write-freeze",
    ):
        assert marker in dependency_checker, marker

    deploy = text("scripts/deploy_release.sh")
    for marker in (
        "requirements.lock",
        "check_dependency_lock.py",
        "--strict",
        "AI_RELEASE_PYTHON",
        "EXPECTED_RUNTIME_PYTHON",
        "EXPECTED_PIP_FREEZE_HASH",
        "Runtime dependency environment hash mismatch",
        "Release bundle must be outside AI_ECOMMERCE_ROOT",
        "AI_RELEASE_REQUIRED=1",
        "runtime_exclusivity_guard.sh",
        "sqlite_backup_rotate.py",
        "/api/system/release-identity",
        "workerReleaseMatch",
        "evidenceSemanticVerified",
    ):
        assert marker in deploy, marker
    for forbidden in ("git reset --hard origin/main", "git fetch", "deploy_v22.sh", "-r \"$TARGET/requirements.txt\""):
        assert forbidden not in deploy, forbidden

    start = text("scripts/start_server.sh")
    for marker in (
        "requirements.lock",
        "check_dependency_lock.py",
        "--strict",
        "AI_RELEASE_REQUIRED",
        "AI_RELEASE_EXPECTED_PYTHON_VERSION",
        "AI_RELEASE_EXPECTED_PIP_FREEZE_HASH",
        "Runtime dependency environment mismatch",
        "ai-release-verifier",
    ):
        assert marker in start, marker

    assert_route_contract()

    workflow = text(".github/workflows/release-hash-seal.yml")
    assert "runs-on: self-hosted" in workflow
    assert "tarball/${RELEASE_SOURCE_COMMIT}" in workflow
    assert "RELEASE_MODE" in workflow
    assert "RELEASE_DEPLOYABLE" in workflow
    for marker in (
        "RELEASE_BASE_PYTHON: /opt/python/3.11.9/bin/python3.11",
        "prepare_exact_venv requirements-dev.lock",
        "RELEASE_RUNTIME_VENV_ROOT",
        "--runtime-python \"$RELEASE_RUNTIME_PYTHON\"",
        "scripts/check_python36_bootstrap.py",
        "ordinary release unexpectedly rotated root trust",
        "ordinaryVerifierRotationRejected",
        "ci-attestation/compile-syntax.log",
        "ci-attestation/static-contract.log",
        "ci-attestation/app-route-smoke.log",
        "ci-attestation/pytest.log",
        "cleanProductionRuntimeVerified",
        "sealed-app-smoke-bundle",
        "release.sealed-app-smoke.v1",
        "AI_RELEASE_REQUIRED=1",
        "runtimeEnvironmentMatch",
        "legacy_runtime_overlay.py",
        "legacy_contract_overlay.py",
        "legacy-gray-proof.log",
        "test_v22_4_app_route_smoke.py",
        "pytest -q",
        "generate_release_manifest.py",
        "release_verifier.py",
    ):
        assert marker in workflow, marker

    status_page = text("web_demo/modules/system-status/page.js")
    for marker in (
        "runtimeEnvironmentMatch",
        "evidenceSemanticVerified",
        "verifiedAttestedFileCount",
        "verifiedTestEvidenceFileCount",
        "extraRuntimeFileCount",
    ):
        assert marker in status_page, marker

    readme = text("README.md")
    assert "Release Hash" in readme
    assert "requirements.lock" in readme
    assert "testEvidenceFiles" in readme
    assert "runtimePipFreezeHash" in readme
    assert "evidenceSemanticVerified" in readme
    assert "ECS只拉取并运行 `origin/main`" not in readme
    assert "scripts/deploy_v22.sh" not in readme
    assert "scripts/verify_release_manifest.py" not in readme

    print("V22.4.0 static release contract check passed without importing runtime code")


if __name__ == "__main__":
    main()
