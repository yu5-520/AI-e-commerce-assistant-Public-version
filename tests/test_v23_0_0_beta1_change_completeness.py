from __future__ import annotations

import json
from pathlib import Path

from tools.registry_compiler.change_manifest import (
    load_change_manifest,
    validate_change_manifest,
)
from tools.registry_compiler.completeness_report import build_completeness_report
from tools.registry_compiler.module_contracts import build_module_contracts


ROOT = Path(__file__).resolve().parents[1]
CHANGE_PATH = ROOT / "contracts" / "changes" / "CHG-V23-BETA1-001.json"


def test_beta1_change_manifest_is_registered_and_approved() -> None:
    manifest = load_change_manifest(CHANGE_PATH)
    validation = validate_change_manifest(manifest, ROOT)
    assert validation["valid"] is True
    assert validation["errors"] == []
    assert validation["manifest"]["changeId"] == "CHG-V23-BETA1-001"
    assert validation["manifest"]["approval"]["status"] == "APPROVED"
    assert validation["changeManifestHash"].startswith("sha256:")


def test_beta1_module_contract_hashes_are_deterministic_and_registry_bound() -> None:
    first = build_module_contracts(ROOT)
    second = build_module_contracts(ROOT)
    registry_manifest = json.loads(
        (ROOT / "contracts" / "registry" / "registry-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert first == second
    assert first["version"] == "23.0.0-beta.1"
    assert first["mode"] == "soft_gate"
    assert first["registryRootHash"] == registry_manifest["registryRootHash"]
    assert first["moduleContractRootHash"].startswith("sha256:")
    compiler = first["moduleContracts"]["registry_compiler"]
    assert compiler["moduleContractHash"].startswith("sha256:")
    implementation = compiler["implementationContentHashes"]
    assert "tools/registry_compiler/compile_registry.py" in implementation
    assert "tools/registry_compiler/completeness_report.py" in implementation
    assert all(value and value.startswith("sha256:") for value in implementation.values())


def test_beta1_self_change_passes_soft_gate() -> None:
    manifest = load_change_manifest(CHANGE_PATH)
    report = build_completeness_report(manifest, ROOT)
    assert report["version"] == "23.0.0-beta.1"
    assert report["mode"] == "soft_gate"
    assert report["softGateStatus"] == "PASS"
    assert report["softGatePassed"] is True
    assert report["deploymentBlocked"] is False
    assert report["requiredChangedModules"] == ["registry_compiler"]
    assert report["pathMapping"]["actualChangedModules"] == ["registry_compiler"]
    assert report["missingRequiredChanges"] == []
    assert report["unexpectedChangedModules"] == []
    assert report["unverifiedAffectedModules"] == []
    assert report["approvedNoCodeOutsideImpact"] == []
    for key in (
        "registryRootHash",
        "changeManifestHash",
        "graphHash",
        "impactHash",
        "moduleContractRootHash",
        "completenessHash",
    ):
        assert report["hashLineage"][key].startswith("sha256:")


def test_beta1_reports_missing_downstream_updates_without_blocking() -> None:
    manifest = {
        "schema": "registry.change_manifest.v1",
        "version": "23.0.0-beta.1",
        "changeId": "TEST-MISSING-DOWNSTREAM",
        "description": "Synthetic field change with intentionally incomplete code paths.",
        "changes": {
            "fields": ["agent1.locked_action_family"],
            "schemas": [],
            "modules": [],
            "interfaces": [],
            "stations": [],
        },
        "expectedImplementationModules": [
            "agent1_runtime",
            "action_pack",
            "agent2_input_projection",
            "agent2_runtime",
            "agent3_input_projection",
            "agent3_runtime",
            "task_mapping",
            "task_pool",
            "frontend_view",
        ],
        "approvedNoCodeChangeModules": [],
        "changedPaths": [
            "src/services/agent_token_runtime_hash_exact_v2259_service.py"
        ],
        "pathModuleHints": {},
        "approval": {
            "status": "APPROVED",
            "approvedBy": "test",
            "approvedAt": "2026-07-28",
            "semanticReviewRequired": True,
        },
    }
    report = build_completeness_report(manifest, ROOT)
    assert report["softGateStatus"] == "WARN"
    assert report["softGatePassed"] is False
    assert report["deploymentBlocked"] is False
    assert report["pathMapping"]["actualChangedModules"] == ["agent1_runtime"]
    assert "action_pack" in report["missingRequiredChanges"]
    assert "frontend_view" in report["missingRequiredChanges"]
    assert report["providerCallsExecuted"] == 0
    assert report["databaseMutated"] is False


def test_beta1_preserves_sealed_release_policy_and_root_verifier_boundary() -> None:
    policy = json.loads(
        (ROOT / "release" / "release-policy.json").read_text(encoding="utf-8")
    )
    assert policy["productVersion"] == "22.4.0"
    assert policy["rules"]["rootVerifierOrdinaryRotationAllowed"] is False
    assert "contracts/changes/**/*" not in set(policy["runtimeGlobs"])
    assert "tools/registry_compiler/**/*" not in set(policy["runtimeGlobs"])
