#!/usr/bin/env python3
"""Python 3.6-compatible root verifier for release.manifest.v1 bundles."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = "release.manifest.v1"
MODE = "single_release_sealed_runtime"
PRODUCT_VERSION = "22.4.0"
PYTHON_VERSION = "3.11.9"

MANIFEST_KEYS = {
    "schema",
    "productVersion",
    "runtimeMode",
    "sourceCommit",
    "runtimeFiles",
    "attestedFiles",
    "testEvidenceFiles",
    "forbiddenPaths",
    "allowedEntrypoints",
    "dependencyLock",
    "buildEnvironment",
    "testRunHash",
    "verificationContract",
    "manifestHash",
    "releaseHash",
}
POLICY_KEYS = {
    "schema",
    "productVersion",
    "runtimeMode",
    "releasePythonVersion",
    "runtimeGlobs",
    "attestedGlobs",
    "excludeGlobs",
    "allowedEntrypoints",
    "forbiddenPaths",
    "rules",
}
FILE_ENTRY_KEYS = {"path", "sha256", "size"}
DEPENDENCY_LOCK_KEYS = {"path", "sha256", "format"}
BUILD_ENVIRONMENT_KEYS = {"pythonVersion", "pipFreezeHash"}
EXPECTED_ENTRYPOINTS = {
    "src.api.main:app",
    "src.services.agent_runtime_hard_interface_v230_service:run_agent_pipeline_tick_hard",
}
EXPECTED_RUNTIME_GLOBS = {
    "src/**/*",
    "web_demo/**/*",
    "config/**/*",
    "scripts/start_server.sh",
    "scripts/runtime_service_resolver.sh",
    "scripts/runtime_exclusivity_guard.sh",
    "scripts/sqlite_backup_rotate.py",
    "scripts/sqlite_data_identity.py",
    "scripts/release_verifier.py",
    "scripts/install_release_verifier.sh",
    "scripts/deploy_release.sh",
    "scripts/check_dependency_lock.py",
    "requirements.txt",
    "requirements.lock",
    "release/release-policy.json",
    "release/release-manifest.schema.json",
    "runtime/java/**/*",
    "scripts/start_v24_authority.sh",
}
EXPECTED_ATTESTED_GLOBS = {
    "tests/**/*",
    "pytest.ini",
    "requirements-dev.lock",
    "scripts/check_release_contract.py",
    "scripts/check_python36_bootstrap.py",
    "scripts/generate_release_manifest.py",
    "scripts/deploy_github_artifact.sh",
    ".github/workflows/release-hash-seal.yml",
    ".github/workflows/historical-contract-audit.yml",
    "README.md",
    "VERSION.md",
    "docs/V22.4.0_RELEASE_HASH_SEAL.md",
    "docs/V22.4.0.7_GITHUB_ARTIFACT_TRANSPORT.md",
    "java-control-plane/src/main/java/**/*",
    "governance/v24/production-authority-bundle-v24.json",
    "scripts/build_v24_production_bundle.sh",
    "scripts/verify_v24_production_bundle.sh",
    ".github/workflows/v24-production-authority-bundle.yml",
}
EXPECTED_EXCLUDE_GLOBS = {
    "**/__pycache__/**",
    "**/*.pyc",
    ".venv/**",
    "logs/**",
    "data/**",
    "outputs/**",
}
REQUIRED_FORBIDDEN_PATHS = {
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
}
EXPECTED_POLICY_RULES = {
    "deployCurrentBranchDirectly": False,
    "manifestRequiredInProduction": True,
    "extraRuntimeFileAllowed": False,
    "forbiddenPathAllowed": False,
    "runtimeAndWorkerReleaseMustMatch": True,
    "deploymentControllerMustComeFromReleaseBundle": True,
    "releaseBundleMustBeOutsideDeploymentRoot": True,
    "dependencyLockRequired": True,
    "runtimeDependencyDriftAllowed": False,
    "attestedFilesMustShipInBundle": True,
    "testEvidenceFilesMustShipInBundle": True,
    "testRunHashMustBeRecomputed": True,
    "exactPythonPatchRequired": True,
    "python36BootstrapCompatibilityRequired": True,
    "rootVerifierOrdinaryRotationAllowed": False,
    "rootVerifierExplicitOldHashRequiredForRotation": True,
    "validatedSqliteBackupRequiredBeforeSwitch": True,
    "sqliteDataIdentityRequiredAfterSwitch": True,
    "sqliteBackupContentHashRequired": True,
    "sqliteSchemaMustMatchDeploymentLineage": True,
    "sharedStateMigrationRequiredOnFirstSeal": True,
    "singleActiveRepositoryServiceRequired": True,
    "strayRepositoryProcessAllowed": False,
    "legacyMutableWorkingTreeAllowedAfterSuccess": False,
}
EXPECTED_VERIFICATION_CONTRACT = {
    "hashAlgorithm": "sha256",
    "canonicalJson": "utf8_sorted_keys_compact",
    "extraRuntimeFileAllowed": False,
    "forbiddenPathAllowed": False,
    "dependencyLockRequired": True,
    "attestedFilesRequired": True,
    "testEvidenceFilesRequired": True,
}
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


def canonical_bytes(manifest):
    payload = dict(
        (key, value)
        for key, value in manifest.items()
        if key not in ("manifestHash", "releaseHash")
    )
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(root, raw):
    relative = Path(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe_release_path:{0}".format(raw))
    resolved = (root / relative).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("release_path_escapes_root:{0}".format(raw))
    return resolved


def load_json(path, object_error):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(object_error)
    return value


def load_evidence_json(path, label, errors):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append("{0}_read_failed:{1}".format(label, exc))
        return {}
    if not isinstance(value, dict):
        errors.append("{0}_must_be_object".format(label))
        return {}
    return value


def exact_keys(value, expected, label, errors):
    if not isinstance(value, dict):
        errors.append("{0}_must_be_object".format(label))
        return
    actual = set(value.keys())
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append("{0}_missing_keys:{1}".format(label, ",".join(missing)))
    if extra:
        errors.append("{0}_extra_keys:{1}".format(label, ",".join(extra)))


def string_list(value, label, errors, allow_empty=False):
    if not isinstance(value, list):
        errors.append("{0}_must_be_list".format(label))
        return []
    result = []
    for item in value:
        if not isinstance(item, str) or not item:
            errors.append("{0}_contains_invalid_string".format(label))
            continue
        result.append(item)
    if not allow_empty and not result:
        errors.append("{0}_must_not_be_empty".format(label))
    if len(result) != len(set(result)):
        errors.append("{0}_contains_duplicates".format(label))
    return result


def policy_paths(root, patterns, excludes):
    result = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                result.add(path.relative_to(root).as_posix())
    for pattern in excludes:
        for path in root.glob(pattern):
            if path.is_file():
                result.discard(path.relative_to(root).as_posix())
    result.discard("release/release-manifest.json")
    return result


def evidence_paths(root):
    evidence_root = root / "release" / "attestation"
    if not evidence_root.is_dir():
        return set()
    return set(
        path.relative_to(root).as_posix()
        for path in evidence_root.rglob("*")
        if path.is_file()
    )


def verify_entries(root, entries, category, global_seen, errors):
    if not isinstance(entries, list):
        errors.append("{0}_files_must_be_list".format(category))
        return 0, set()
    if not entries:
        errors.append("{0}_files_empty".format(category))
        return 0, set()
    verified = 0
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("{0}_file_entry_must_be_object".format(category))
            continue
        exact_keys(entry, FILE_ENTRY_KEYS, "{0}_file_entry".format(category), errors)
        raw_value = entry.get("path")
        raw = raw_value if isinstance(raw_value, str) else ""
        if raw in global_seen:
            errors.append("duplicate_release_file:{0}".format(raw))
            continue
        global_seen.add(raw)
        seen.add(raw)
        try:
            path = safe_path(root, raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path.is_symlink():
            errors.append("{0}_file_symlink_forbidden:{1}".format(category, raw))
            continue
        if not path.is_file():
            errors.append("{0}_file_missing:{1}".format(category, raw))
            continue
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            errors.append("invalid_{0}_file_hash:{1}".format(category, raw))
            continue
        expected_size = entry.get("size")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
            errors.append("invalid_{0}_file_size:{1}".format(category, raw))
            continue
        if path.stat().st_size != expected_size:
            errors.append("{0}_file_size_mismatch:{1}".format(category, raw))
            continue
        if hash_file(path) != expected_hash:
            errors.append("{0}_file_hash_mismatch:{1}".format(category, raw))
            continue
        verified += 1
    return verified, seen


def expected_test_run_hash(entries):
    if not isinstance(entries, list) or not entries:
        return None
    digest = hashlib.sha256()
    normalized = sorted(
        entries,
        key=lambda item: str(item.get("path") or "") if isinstance(item, dict) else "",
    )
    for entry in normalized:
        if not isinstance(entry, dict):
            return None
        path = entry.get("path")
        file_hash = entry.get("sha256")
        if not isinstance(path, str) or not path:
            return None
        if not isinstance(file_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", file_hash):
            return None
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file_hash))
    return "sha256:" + digest.hexdigest()


def expected_attested_digest(entries):
    if not isinstance(entries, list) or not entries:
        return None
    lines = []
    for entry in sorted(
        entries,
        key=lambda item: str(item.get("path") or "") if isinstance(item, dict) else "",
    ):
        if not isinstance(entry, dict):
            return None
        raw = entry.get("path")
        file_hash = entry.get("sha256")
        if not isinstance(raw, str) or not raw:
            return None
        if not isinstance(file_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", file_hash):
            return None
        lines.append("{0}  {1}".format(file_hash, raw))
    return "\n".join(lines) + "\n"


def verify_evidence_semantics(root, manifest, attested_entries, evidence_entries, errors):
    start_error_count = len(errors)
    evidence_root = root / "release" / "attestation"
    entry_map = {}
    if isinstance(evidence_entries, list):
        for entry in evidence_entries:
            if isinstance(entry, dict):
                entry_map[str(entry.get("path") or "")] = entry

    for relative in REQUIRED_EVIDENCE_PATHS:
        manifest_path = "release/attestation/{0}".format(relative)
        entry = entry_map.get(manifest_path)
        if entry is None:
            errors.append("required_test_evidence_not_manifested:{0}".format(relative))
            continue
        size = entry.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            errors.append("required_test_evidence_empty:{0}".format(relative))

    source_commit = str(manifest.get("sourceCommit") or "")
    build_environment = manifest.get("buildEnvironment") or {}
    python_version = str(build_environment.get("pythonVersion") or "")
    pip_freeze_hash = str(build_environment.get("pipFreezeHash") or "")

    test_attestation = load_evidence_json(
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
        REQUIRED_EVIDENCE_PATHS
    ):
        errors.append("test_attestation_required_evidence_mismatch")
    for flag in REQUIRED_TEST_ATTESTATION_FLAGS:
        expected = False if flag == "staticCheckerImportedRuntime" else True
        if test_attestation.get(flag) is not expected:
            errors.append("test_attestation_flag_mismatch:{0}".format(flag))

    production_runtime = load_evidence_json(
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
            errors.append("production_runtime_{0}_not_empty".format(field))

    python_runtime = load_evidence_json(
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
    if python_runtime.get("isolatedRuntime") is not True:
        errors.append("python_runtime_not_isolated")

    freeze_path = evidence_root / "pip-freeze.txt"
    if freeze_path.is_file() and "sha256:" + hash_file(freeze_path) != pip_freeze_hash:
        errors.append("pip_freeze_evidence_hash_mismatch")

    digest_path = evidence_root / "attested-files.sha256"
    expected_digest = expected_attested_digest(attested_entries)
    if expected_digest is None:
        errors.append("attested_source_digest_invalid")
    elif digest_path.is_file() and digest_path.read_text(encoding="utf-8") != expected_digest:
        errors.append("attested_source_digest_mismatch")

    return len(errors) == start_error_count


def verify(root, manifest_path):
    errors = []
    if manifest_path.is_symlink():
        errors.append("release_manifest_symlink_forbidden")
    manifest = load_json(manifest_path, "release_manifest_must_be_object")
    policy_path = root / "release" / "release-policy.json"
    if policy_path.is_symlink():
        errors.append("release_policy_symlink_forbidden")
    policy = load_json(policy_path, "release_policy_must_be_object") if policy_path.is_file() else {}

    exact_keys(manifest, MANIFEST_KEYS, "release_manifest", errors)
    exact_keys(policy, POLICY_KEYS, "release_policy", errors)

    if manifest.get("schema") != SCHEMA:
        errors.append("release_manifest_schema_mismatch")
    if manifest.get("productVersion") != PRODUCT_VERSION:
        errors.append("release_product_version_mismatch")
    if manifest.get("runtimeMode") != MODE:
        errors.append("release_runtime_mode_mismatch")
    if policy.get("schema") != "release.policy.v1":
        errors.append("release_policy_schema_mismatch")
    if policy.get("productVersion") != manifest.get("productVersion"):
        errors.append("release_policy_product_version_mismatch")
    if policy.get("runtimeMode") != manifest.get("runtimeMode"):
        errors.append("release_policy_runtime_mode_mismatch")
    if policy.get("releasePythonVersion") != PYTHON_VERSION:
        errors.append("release_policy_python_version_mismatch")
    if policy.get("rules") != EXPECTED_POLICY_RULES:
        errors.append("release_policy_rules_mismatch")

    runtime_globs = string_list(policy.get("runtimeGlobs"), "release_policy_runtime_globs", errors)
    attested_globs = string_list(policy.get("attestedGlobs"), "release_policy_attested_globs", errors)
    exclude_globs = string_list(policy.get("excludeGlobs"), "release_policy_exclude_globs", errors)
    policy_entrypoints = string_list(policy.get("allowedEntrypoints"), "release_policy_entrypoints", errors)
    policy_forbidden = string_list(policy.get("forbiddenPaths"), "release_policy_forbidden_paths", errors)
    manifest_entrypoints = string_list(manifest.get("allowedEntrypoints"), "release_manifest_entrypoints", errors)
    manifest_forbidden = string_list(manifest.get("forbiddenPaths"), "release_manifest_forbidden_paths", errors)

    if set(runtime_globs) != EXPECTED_RUNTIME_GLOBS:
        errors.append("release_policy_runtime_globs_mismatch")
    if set(attested_globs) != EXPECTED_ATTESTED_GLOBS:
        errors.append("release_policy_attested_globs_mismatch")
    if set(exclude_globs) != EXPECTED_EXCLUDE_GLOBS:
        errors.append("release_policy_exclude_globs_mismatch")
    if set(policy_entrypoints) != EXPECTED_ENTRYPOINTS:
        errors.append("release_policy_entrypoints_invalid")
    if set(manifest_entrypoints) != EXPECTED_ENTRYPOINTS:
        errors.append("release_manifest_entrypoints_invalid")
    if policy_entrypoints != manifest_entrypoints:
        errors.append("release_policy_entrypoints_mismatch")
    if set(policy_forbidden) != REQUIRED_FORBIDDEN_PATHS:
        errors.append("release_policy_forbidden_paths_invalid")
    if policy_forbidden != manifest_forbidden:
        errors.append("release_policy_forbidden_paths_mismatch")

    source_commit = manifest.get("sourceCommit")
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        errors.append("release_source_commit_invalid")
        source_commit = str(source_commit or "")

    canonical = canonical_bytes(manifest)
    expected_manifest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    expected_release = "sha256:" + hashlib.sha256(SCHEMA.encode("utf-8") + b"\0" + canonical).hexdigest()
    if manifest.get("manifestHash") != expected_manifest:
        errors.append("release_manifest_hash_mismatch")
    if manifest.get("releaseHash") != expected_release:
        errors.append("release_root_hash_mismatch")

    verification_contract = manifest.get("verificationContract")
    exact_keys(
        verification_contract,
        set(EXPECTED_VERIFICATION_CONTRACT.keys()),
        "release_verification_contract",
        errors,
    )
    if verification_contract != EXPECTED_VERIFICATION_CONTRACT:
        errors.append("release_verification_contract_mismatch")

    dependency_lock = manifest.get("dependencyLock")
    exact_keys(dependency_lock, DEPENDENCY_LOCK_KEYS, "release_dependency_lock", errors)
    dependency_lock = dependency_lock if isinstance(dependency_lock, dict) else {}
    dependency_path = dependency_lock.get("path")
    dependency_hash = dependency_lock.get("sha256")
    if dependency_path != "requirements.lock":
        errors.append("release_dependency_lock_path_invalid")
    elif not isinstance(dependency_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", dependency_hash):
        errors.append("release_dependency_lock_hash_invalid")
    else:
        try:
            lock_path = safe_path(root, dependency_path)
        except ValueError as exc:
            errors.append(str(exc))
            lock_path = None
        if lock_path is not None:
            if lock_path.is_symlink():
                errors.append("release_dependency_lock_symlink_forbidden")
            elif not lock_path.is_file():
                errors.append("release_dependency_lock_missing")
            elif hash_file(lock_path) != dependency_hash:
                errors.append("release_dependency_lock_hash_mismatch")
    if dependency_lock.get("format") != "pip-exact-pins-v1":
        errors.append("release_dependency_lock_format_invalid")

    build_environment = manifest.get("buildEnvironment")
    exact_keys(build_environment, BUILD_ENVIRONMENT_KEYS, "release_build_environment", errors)
    build_environment = build_environment if isinstance(build_environment, dict) else {}
    if build_environment.get("pythonVersion") != PYTHON_VERSION:
        errors.append("release_python_version_invalid")
    if not isinstance(build_environment.get("pipFreezeHash"), str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", build_environment.get("pipFreezeHash") or ""
    ):
        errors.append("release_pip_freeze_hash_invalid")

    global_seen = set()
    runtime_entries = manifest.get("runtimeFiles")
    attested_entries = manifest.get("attestedFiles")
    evidence_entries = manifest.get("testEvidenceFiles")
    runtime_verified, runtime_seen = verify_entries(
        root, runtime_entries, "release", global_seen, errors
    )
    attested_verified, attested_seen = verify_entries(
        root, attested_entries, "attested", global_seen, errors
    )
    evidence_verified, evidence_seen = verify_entries(
        root, evidence_entries, "test_evidence", global_seen, errors
    )

    calculated_test_run_hash = expected_test_run_hash(evidence_entries)
    if calculated_test_run_hash is None:
        errors.append("release_test_evidence_invalid")
    elif manifest.get("testRunHash") != calculated_test_run_hash:
        errors.append("release_test_run_hash_mismatch")

    evidence_semantic_verified = verify_evidence_semantics(
        root, manifest, attested_entries, evidence_entries, errors
    )

    forbidden = []
    for raw in manifest_forbidden:
        try:
            path = safe_path(root, raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if path.exists() or path.is_symlink():
            forbidden.append(raw)
            errors.append("forbidden_release_path_present:{0}".format(raw))

    actual_runtime = policy_paths(root, runtime_globs, exclude_globs)
    extra_runtime = sorted(actual_runtime - runtime_seen)
    missing_runtime_policy = sorted(runtime_seen - actual_runtime)
    errors.extend("extra_runtime_file:{0}".format(item) for item in extra_runtime)
    errors.extend(
        "manifest_runtime_file_outside_policy:{0}".format(item)
        for item in missing_runtime_policy
    )

    actual_attested = policy_paths(root, attested_globs, exclude_globs)
    extra_attested = sorted(actual_attested - attested_seen)
    missing_attested_policy = sorted(attested_seen - actual_attested)
    errors.extend("extra_attested_file:{0}".format(item) for item in extra_attested)
    errors.extend(
        "manifest_attested_file_outside_policy:{0}".format(item)
        for item in missing_attested_policy
    )

    actual_evidence = evidence_paths(root)
    extra_evidence = sorted(actual_evidence - evidence_seen)
    missing_evidence = sorted(evidence_seen - actual_evidence)
    errors.extend(
        "extra_test_evidence_file:{0}".format(item) for item in extra_evidence
    )
    errors.extend(
        "manifest_test_evidence_file_missing:{0}".format(item)
        for item in missing_evidence
    )

    runtime_count = len(runtime_entries) if isinstance(runtime_entries, list) else 0
    attested_count = len(attested_entries) if isinstance(attested_entries, list) else 0
    evidence_count = len(evidence_entries) if isinstance(evidence_entries, list) else 0
    return {
        "schema": "release.verification.v1",
        "verified": not errors,
        "status": "verified" if not errors else "verification_failed",
        "productVersion": manifest.get("productVersion"),
        "sourceCommit": source_commit,
        "manifestHash": manifest.get("manifestHash"),
        "releaseHash": manifest.get("releaseHash"),
        "testRunHash": manifest.get("testRunHash"),
        "calculatedTestRunHash": calculated_test_run_hash,
        "evidenceSemanticVerified": evidence_semantic_verified,
        "dependencyLockHash": dependency_hash,
        "pythonVersion": build_environment.get("pythonVersion"),
        "pipFreezeHash": build_environment.get("pipFreezeHash"),
        "runtimeRoot": str(root),
        "manifestPath": str(manifest_path),
        "verifiedFileCount": runtime_verified,
        "manifestFileCount": runtime_count,
        "verifiedAttestedFileCount": attested_verified,
        "attestedFileCount": attested_count,
        "verifiedTestEvidenceFileCount": evidence_verified,
        "testEvidenceFileCount": evidence_count,
        "forbiddenPathViolations": forbidden,
        "extraRuntimeFiles": extra_runtime,
        "extraAttestedFiles": extra_attested,
        "extraTestEvidenceFiles": extra_evidence,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--manifest")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    manifest = (
        Path(args.manifest).resolve()
        if args.manifest
        else root / "release" / "release-manifest.json"
    )
    try:
        result = verify(root, manifest)
    except Exception as exc:
        result = {
            "schema": "release.verification.v1",
            "verified": False,
            "status": "verification_error",
            "runtimeRoot": str(root),
            "manifestPath": str(manifest),
            "errors": ["release_verification_exception:{0}".format(exc)],
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("verified") is True else 1


if __name__ == "__main__":
    sys.exit(main())
