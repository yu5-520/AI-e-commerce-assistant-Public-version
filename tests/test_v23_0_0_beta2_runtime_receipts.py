from __future__ import annotations

import copy
import json
from pathlib import Path

from tools.registry_compiler.change_manifest import (
    load_change_manifest,
    validate_change_manifest,
)
from tools.registry_compiler.completeness_report import build_completeness_report
from tools.registry_compiler.runtime_receipts import (
    build_runtime_receipt_set,
    compare_environment_receipts,
    load_runtime_receipt_set,
    persist_runtime_receipt_set,
    verify_runtime_receipt_set,
)


ROOT = Path(__file__).resolve().parents[1]
CHANGE_PATH = ROOT / "contracts" / "changes" / "CHG-V23-BETA2-001.json"
RELEASE_COMMIT = "0b8667c513c95d4b7b57e0603dfb661d2ab8222e"
CAPTURED_AT = "2026-07-28T00:00:00Z"
MODULES = ["registry_compiler", "agent1_runtime", "agent2_runtime"]


def _receipt(environment: str) -> dict:
    return build_runtime_receipt_set(
        ROOT,
        environment=environment,
        release_commit=RELEASE_COMMIT,
        modules=MODULES,
        captured_at=CAPTURED_AT,
        source="unit_test",
    )


def test_beta2_change_manifest_and_completeness_soft_gate_pass() -> None:
    manifest = load_change_manifest(CHANGE_PATH)
    validation = validate_change_manifest(manifest, ROOT)
    report = build_completeness_report(manifest, ROOT)
    assert validation["valid"] is True
    assert validation["manifest"]["changeId"] == "CHG-V23-BETA2-001"
    assert validation["manifest"]["approval"]["status"] == "APPROVED"
    assert report["softGateStatus"] == "PASS"
    assert report["softGatePassed"] is True
    assert report["deploymentBlocked"] is False
    assert report["pathMapping"]["actualChangedModules"] == ["registry_compiler"]
    assert report["missingRequiredChanges"] == []
    assert report["unverifiedAffectedModules"] == []


def test_beta2_runtime_receipt_set_is_deterministic_and_verifiable() -> None:
    first = _receipt("gray")
    second = _receipt("gray")
    assert first == second
    assert first["version"] == "23.0.0-beta.2"
    assert first["mode"] == "soft_gate"
    assert first["environment"] == "gray"
    assert first["requiredModules"] == sorted(MODULES)
    assert first["receiptSetHash"].startswith("sha256:")
    assert all(item["receiptHash"].startswith("sha256:") for item in first["receipts"])

    verification = verify_runtime_receipt_set(
        first,
        ROOT,
        expected_environment="gray",
        expected_release_commit=RELEASE_COMMIT,
        required_modules=MODULES,
    )
    assert verification["verified"] is True
    assert verification["softGateStatus"] == "PASS"
    assert verification["deploymentBlocked"] is False
    assert verification["errors"] == []
    assert verification["receiptErrors"] == {}
    assert verification["verificationHash"].startswith("sha256:")


def test_beta2_receipt_set_can_be_persisted_and_loaded(tmp_path: Path) -> None:
    receipt = _receipt("repository_validation")
    target = tmp_path / "receipts" / "repository-validation.json"
    persisted = persist_runtime_receipt_set(receipt, target, tmp_path)
    loaded = load_runtime_receipt_set(persisted)
    assert loaded == receipt


def test_beta2_gray_and_production_receipts_have_full_parity() -> None:
    gray = _receipt("gray")
    production = _receipt("production")
    comparison = compare_environment_receipts(
        gray,
        production,
        ROOT,
        required_modules=MODULES,
    )
    assert comparison["softGateStatus"] == "PASS"
    assert comparison["softGatePassed"] is True
    assert comparison["deploymentBlocked"] is False
    assert comparison["releaseCommitMatch"] is True
    assert comparison["parityMismatches"] == {}
    assert comparison["gray"]["verified"] is True
    assert comparison["production"]["verified"] is True
    assert comparison["comparisonHash"].startswith("sha256:")


def test_beta2_detects_contract_drift_without_blocking() -> None:
    gray = _receipt("gray")
    production = copy.deepcopy(_receipt("production"))
    production["receipts"][0]["moduleContractHash"] = "sha256:" + "0" * 64

    comparison = compare_environment_receipts(
        gray,
        production,
        ROOT,
        required_modules=MODULES,
    )
    assert comparison["softGateStatus"] == "WARN"
    assert comparison["softGatePassed"] is False
    assert comparison["deploymentBlocked"] is False
    assert comparison["production"]["verified"] is False
    assert comparison["parityMismatches"]


def test_beta2_detects_missing_required_receipt() -> None:
    gray = _receipt("gray")
    gray["receipts"] = [
        item for item in gray["receipts"] if item["moduleId"] != "agent2_runtime"
    ]
    verification = verify_runtime_receipt_set(
        gray,
        ROOT,
        expected_environment="gray",
        required_modules=MODULES,
    )
    assert verification["verified"] is False
    assert verification["softGateStatus"] == "WARN"
    assert "agent2_runtime" in verification["missingModules"]
    assert verification["deploymentBlocked"] is False


def test_beta2_preserves_release_policy_and_root_verifier_boundary() -> None:
    policy = json.loads(
        (ROOT / "release" / "release-policy.json").read_text(encoding="utf-8")
    )
    assert policy["productVersion"] == "22.4.0"
    assert policy["rules"]["rootVerifierOrdinaryRotationAllowed"] is False
    assert "contracts/receipts/**/*" not in set(policy["runtimeGlobs"])
    assert "tools/registry_compiler/**/*" not in set(policy["runtimeGlobs"])
