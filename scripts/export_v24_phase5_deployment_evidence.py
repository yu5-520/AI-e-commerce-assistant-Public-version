#!/usr/bin/env python3
"""Export static V24.18-V24.20 deployment/compatibility/legacy evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "src" / "deployment" / "deploy_release_core_v22516.sh"
GUARD = ROOT / "scripts" / "runtime_exclusivity_guard.sh"
CALLABLE_AUTH = ROOT / "config" / "deployment" / "runtime_callable_authority_v1.json"
RELEASE_CONTRACT = ROOT / "scripts" / "check_release_contract.py"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def source_commit() -> str | None:
    marker = ROOT / ".v24-phase5-source-commit"
    if marker.is_file():
        value = marker.read_text(encoding="utf-8").strip()
        return value or None
    return None


def build() -> dict[str, Any]:
    deploy = DEPLOY.read_text(encoding="utf-8")
    guard = GUARD.read_text(encoding="utf-8")
    callable_authority = json.loads(CALLABLE_AUTH.read_text(encoding="utf-8"))
    release_contract = RELEASE_CONTRACT.read_text(encoding="utf-8")

    current_symlink = 'ln -sfn "$TARGET" "$ROOT_DIR/current"' in deploy
    systemd_switch = 'systemctl stop "$SERVICE"' in deploy and 'systemctl start "$SERVICE"' in deploy
    rollback_present = "rollback_on_error()" in deploy and "restore_database_and_lineage" in deploy
    exact_python = "EXPECTED_RUNTIME_PYTHON" in deploy and "BASE_PYTHON_VERSION" in deploy
    dependency_hash = "EXPECTED_PIP_FREEZE_HASH" in deploy and "ACTUAL_PIP_FREEZE_HASH" in deploy
    schema_proof = "PREPARED_SCHEMA_HASH" in deploy and "schemaPreparedBeforeLineage" in deploy
    release_identity = "workerReleaseMatch" in deploy and "/api/system/release-identity" in deploy
    data_identity = "/api/system/data-identity" in deploy and "schemaMatch" in deploy
    forbidden_retirement = "retire_forbidden_legacy_paths" in deploy and "retire_forbidden_legacy_paths()" in guard
    working_tree_retirement = "retire_legacy_working_tree_after_success" in deploy and "retire_legacy_working_tree_after_success()" in guard
    runtime_unit_retirement = "retire_all_shadow_runtime_units" in deploy and "retire_stray_repository_runtime_processes" in deploy
    java_in_production = "com.zcentury.v24" in deploy or "java-control-plane" in deploy
    callable_overlay = callable_authority.get("legacyOverlay") or {}
    overlay_verification_only = callable_overlay.get("mutationAllowed") is False and callable_overlay.get("mode") == "verification_only"
    release_contract_present = "release" in release_contract.lower() and "hash" in release_contract.lower()

    require(current_symlink and systemd_switch, "sealed_release_systemd_cutover_not_found")
    require(rollback_present, "deployment_rollback_not_found")
    require(exact_python and dependency_hash and schema_proof and release_identity and data_identity, "distributed_compatibility_assertions_not_found")
    require(forbidden_retirement and working_tree_retirement and runtime_unit_retirement, "legacy_retirement_paths_not_found")
    require(overlay_verification_only, "legacy_overlay_verification_only_contract_not_found")
    require(release_contract_present, "release_contract_checker_not_found")
    require(not java_in_production, "unexpected_java_deployment_authority_already_in_production")

    evidence: dict[str, Any] = {
        "schema": "v24.phase5_deployment_baseline.evidence.v1",
        "version": "24.20.0",
        "verified": True,
        "sourceCommit": source_commit(),
        "productionDeploymentAuthority": "BASH_SYSTEMD_ROOT",
        "compatibilityAuthority": "BASH_PLUS_PYTHON_ASSERTIONS",
        "legacyRetirementAuthority": "BASH_PYTHON_RUNTIME_EXCLUSIVITY_GUARD",
        "currentSymlinkCutover": current_symlink,
        "systemdServiceSwitch": systemd_switch,
        "rollbackPresent": rollback_present,
        "exactPythonCompatibilityCheck": exact_python,
        "dependencyHashCompatibilityCheck": dependency_hash,
        "schemaCompatibilityProof": schema_proof,
        "releaseIdentityCompatibilityCheck": release_identity,
        "dataIdentityCompatibilityCheck": data_identity,
        "legacyForbiddenPathRetirement": forbidden_retirement,
        "legacyWorkingTreeRetirement": working_tree_retirement,
        "legacyRuntimeOwnerRetirement": runtime_unit_retirement,
        "legacyOverlayVerificationOnly": overlay_verification_only,
        "javaDeploymentAuthorityInProduction": java_in_production,
        "migrationNeed": {
            "deploymentDecisionMustBecomeOneAuthority": True,
            "compatibilityMustBecomeExplicitContract": True,
            "deploymentPublishNeedsCas": True,
            "deploymentNeedsGenerationFence": True,
            "legacyRemovalNeedsReplacementProof": True,
            "legacyRemovalNeedsZeroRightsProof": True,
            "legacyAutoFallbackMustBeForbidden": True,
        },
        "sourceFiles": {
            "deploy": {"path": str(DEPLOY.relative_to(ROOT)), "sha256": file_hash(DEPLOY)},
            "guard": {"path": str(GUARD.relative_to(ROOT)), "sha256": file_hash(GUARD)},
            "callableAuthority": {"path": str(CALLABLE_AUTH.relative_to(ROOT)), "sha256": file_hash(CALLABLE_AUTH)},
            "releaseContract": {"path": str(RELEASE_CONTRACT.relative_to(ROOT)), "sha256": file_hash(RELEASE_CONTRACT)},
        },
    }
    evidence["evidenceHash"] = sha256_value(evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/v24-java-phase5/deployment-baseline-evidence.json")
    args = parser.parse_args()
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    evidence = build()
    output.write_text(canonical(evidence) + "\n", encoding="utf-8")
    print(canonical({
        "verified": evidence["verified"],
        "productionDeploymentAuthority": evidence["productionDeploymentAuthority"],
        "compatibilityAuthority": evidence["compatibilityAuthority"],
        "legacyRetirementAuthority": evidence["legacyRetirementAuthority"],
        "javaDeploymentAuthorityInProduction": evidence["javaDeploymentAuthorityInProduction"],
        "evidenceHash": evidence["evidenceHash"],
    }))


if __name__ == "__main__":
    main()
