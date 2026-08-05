from __future__ import annotations

import json
import shutil
from pathlib import Path

from src.services.registry_runtime_receipt_v23_service import (
    build_selected_module_contracts,
    load_runtime_projection,
    run_startup_gate,
)
from tools.registry_compiler.change_manifest import (
    load_change_manifest,
    validate_change_manifest,
)
from tools.registry_compiler.completeness_report import build_completeness_report


ROOT = Path(__file__).resolve().parents[1]
CHANGE_PATH = ROOT / "contracts" / "changes" / "CHG-V23-RC1-001.json"
RELEASE_COMMIT = "f282e20de57434541617986a7232d2031f419b06"
RELEASE_HASH = "sha256:" + "1" * 64
MANIFEST_HASH = "sha256:" + "2" * 64
CAPTURED_AT = "2026-07-28T00:00:00Z"


def _copy(relative: str, target_root: Path) -> None:
    source = ROOT / relative
    target = target_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _release_root(tmp_path: Path) -> Path:
    root = tmp_path / "release-root"
    projection = load_runtime_projection(ROOT)
    paths = {
        "config/v23_registry_runtime.json",
        "release/release-policy.json",
        "src/services/registry_runtime_receipt_v23_service.py",
    }
    for module in projection["modules"].values():
        runner_module = str(module["runner"]).partition(":")[0]
        paths.add(str(Path(*runner_module.split(".")).with_suffix(".py")))
        paths.update(str(path) for path in module.get("implementationPaths") or [])
    for relative in sorted(paths):
        _copy(relative, root)
    manifest = {
        "sourceCommit": RELEASE_COMMIT,
        "releaseHash": RELEASE_HASH,
        "manifestHash": MANIFEST_HASH,
    }
    manifest_path = root / "release" / "release-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


def test_rc1_change_manifest_and_completeness_pass() -> None:
    manifest = load_change_manifest(CHANGE_PATH)
    validation = validate_change_manifest(manifest, ROOT)
    report = build_completeness_report(manifest, ROOT)
    assert validation["valid"] is True
    assert validation["manifest"]["changeId"] == "CHG-V23-RC1-001"
    assert validation["manifest"]["approval"]["status"] == "APPROVED"
    assert report["softGatePassed"] is True
    assert report["missingRequiredChanges"] == []
    assert report["unexpectedChangedModules"] == []
    assert report["unverifiedAffectedModules"] == []
    assert set(report["pathMapping"]["actualChangedModules"]) == {
        "registry_compiler",
        "release_governance",
    }


def test_rc1_projection_is_registry_bound_and_selected() -> None:
    projection = load_runtime_projection(ROOT)
    registry_manifest = json.loads(
        (ROOT / "contracts" / "registry" / "registry-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert projection["version"] == "23.0.0-rc.1"
    assert projection["mode"] == "selected_fail_closed"
    assert projection["registryRootHash"] == registry_manifest["registryRootHash"]
    assert projection["requiredModules"] == [
        "agent1_runtime",
        "release_governance",
    ]
    assert projection["rules"]["deploymentMustFailClosed"] is True
    assert "agent2_runtime" not in projection["requiredModules"]


def test_rc1_selected_contracts_resolve_runners_and_implementation_hashes() -> None:
    contracts = build_selected_module_contracts(ROOT)
    assert contracts["verified"] is True
    assert contracts["errors"] == []
    assert contracts["moduleContractRootHash"].startswith("sha256:")
    assert set(contracts["moduleContracts"]) == {
        "agent1_runtime",
        "release_governance",
    }
    for contract in contracts["moduleContracts"].values():
        assert contract["runnerFileExists"] is True
        assert contract["runnerSymbolExists"] is True
        assert contract["moduleContractHash"].startswith("sha256:")
        hashes = contract["implementationContentHashes"]
        assert hashes
        assert all(value and value.startswith("sha256:") for value in hashes.values())


def test_rc1_gray_and_production_startup_gates_pass(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    receipt_root = tmp_path / "receipts"
    gray_receipt = receipt_root / "gray.json"
    gray_report = receipt_root / "gray-gate.json"
    gray = run_startup_gate(
        root,
        environment="gray",
        release_commit=RELEASE_COMMIT,
        release_hash=RELEASE_HASH,
        receipt_output=gray_receipt,
        report_output=gray_report,
        allowed_output_roots=[receipt_root],
        captured_at=CAPTURED_AT,
        source="unit_test_gray",
    )
    assert gray["hardGateStatus"] == "PASS"
    assert gray["hardGatePassed"] is True
    assert gray["deploymentBlocked"] is False
    assert gray_receipt.is_file()
    assert gray_report.is_file()

    production = run_startup_gate(
        root,
        environment="production",
        release_commit=RELEASE_COMMIT,
        release_hash=RELEASE_HASH,
        receipt_output=receipt_root / "production.json",
        report_output=receipt_root / "production-gate.json",
        gray_receipt_path=gray_receipt,
        allowed_output_roots=[receipt_root],
        captured_at=CAPTURED_AT,
        source="unit_test_production",
    )
    assert production["hardGateStatus"] == "PASS"
    assert production["hardGatePassed"] is True
    assert production["deploymentBlocked"] is False
    assert production["environmentComparison"]["passed"] is True
    assert production["hardGateHash"].startswith("sha256:")


def test_rc1_tampered_gray_receipt_blocks_production(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    receipt_root = tmp_path / "receipts"
    gray_path = receipt_root / "gray.json"
    gray = run_startup_gate(
        root,
        environment="gray",
        release_commit=RELEASE_COMMIT,
        release_hash=RELEASE_HASH,
        receipt_output=gray_path,
        report_output=receipt_root / "gray-gate.json",
        allowed_output_roots=[receipt_root],
        captured_at=CAPTURED_AT,
    )
    assert gray["hardGatePassed"] is True

    tampered = json.loads(gray_path.read_text(encoding="utf-8"))
    tampered["receipts"][0]["moduleContractHash"] = "sha256:" + "0" * 64
    gray_path.write_text(
        json.dumps(tampered, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    production = run_startup_gate(
        root,
        environment="production",
        release_commit=RELEASE_COMMIT,
        release_hash=RELEASE_HASH,
        receipt_output=receipt_root / "production.json",
        report_output=receipt_root / "production-gate.json",
        gray_receipt_path=gray_path,
        allowed_output_roots=[receipt_root],
        captured_at=CAPTURED_AT,
    )
    assert production["hardGateStatus"] == "BLOCK"
    assert production["hardGatePassed"] is False
    assert production["deploymentBlocked"] is True
    assert "gray_production_selected_module_parity_failed" in production["failures"]


def test_rc1_preserves_pinned_release_policy_and_root_verifier() -> None:
    policy = json.loads(
        (ROOT / "release" / "release-policy.json").read_text(encoding="utf-8")
    )
    runtime = set(policy["runtimeGlobs"])
    assert policy["productVersion"] == "22.4.0"
    assert policy["rules"]["rootVerifierOrdinaryRotationAllowed"] is False
    assert "src/**/*" in runtime
    assert "config/**/*" in runtime
    assert "tools/registry_compiler/**/*" not in runtime
    assert "contracts/registry/**/*" not in runtime
    assert "contracts/receipts/**/*" not in runtime
    assert (ROOT / "src/services/registry_runtime_receipt_v23_service.py").is_file()
    assert (ROOT / "config/v23_registry_runtime.json").is_file()


def test_rc1_startup_and_deployment_scripts_are_fail_closed() -> None:
    deploy = (ROOT / "scripts" / "deploy_release.sh").read_text(encoding="utf-8")
    startup = (ROOT / "scripts" / "start_server.sh").read_text(encoding="utf-8")
    assert "V23 RC1 gray receipt hard gate" in deploy
    assert "deployment_gray_preflight" in deploy
    assert "gray-${SOURCE_COMMIT}.json" in deploy
    assert "src.services.registry_runtime_receipt_v23_service" in deploy
    assert "--release-hash" in deploy
    assert "V23 RC1 production startup receipt gate blocked runtime" in startup
    assert "src.services.registry_runtime_receipt_v23_service" in startup
    assert "--environment production" in startup
    assert "--release-hash" in startup
    assert "--gray-receipt" in startup
