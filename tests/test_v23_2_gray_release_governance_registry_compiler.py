from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.services import registry_runtime_receipt_v23_service as gate

ROOT = Path(__file__).resolve().parents[1]
_SOURCE_COMMIT = "a" * 40
_RELEASE_HASH = "sha256:" + "b" * 64
_MANIFEST_HASH = "sha256:" + "c" * 64


def _identity() -> dict[str, str]:
    return {
        "sourceCommit": _SOURCE_COMMIT,
        "releaseHash": _RELEASE_HASH,
        "manifestHash": _MANIFEST_HASH,
    }


def _build_gray_candidate(tmp_path: Path) -> Path:
    candidate = tmp_path / "candidate"
    config_relative = Path("config/v23_registry_runtime.json")
    config = json.loads((ROOT / config_relative).read_text(encoding="utf-8"))
    required_paths = {
        config_relative.as_posix(),
        "src/services/registry_runtime_receipt_v23_service.py",
    }
    for module_id in config["requiredModules"]:
        definition = config["modules"][module_id]
        required_paths.update(definition.get("implementationPaths") or [])
        runner_module = str(definition["runner"]).partition(":")[0]
        required_paths.add(runner_module.replace(".", "/") + ".py")

    for relative in sorted(required_paths):
        source = ROOT / relative
        assert source.is_file(), relative
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    manifest_path = candidate / "release/release-manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(_identity(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return candidate


def test_gray_cli_runs_by_file_path_without_pythonpath_or_src_import(tmp_path: Path) -> None:
    candidate = _build_gray_candidate(tmp_path)
    receipt = tmp_path / "gray.json"
    report = tmp_path / "gray-gate.json"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "AI_RELEASE_ROOT": str(candidate),
        "PYTHONNOUSERSITE": "1",
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(candidate / "src/services/registry_runtime_receipt_v23_service.py"),
            "--environment",
            "gray",
            "--release-commit",
            _SOURCE_COMMIT,
            "--release-hash",
            _RELEASE_HASH,
            "--output",
            str(receipt),
            "--report",
            str(report),
            "--allowed-output-root",
            str(tmp_path),
            "--source",
            "pytest_gray_bootstrap",
        ],
        cwd=candidate,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "No module named 'src'" not in completed.stderr
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    agent1 = next(
        item for item in payload["receipts"] if item["moduleId"] == "agent1_runtime"
    )
    binding = agent1["activeBindingProbe"]
    assert binding["verificationMode"] == "static_ast"
    assert binding["evidence"]["businessRuntimeImported"] is False
    assert binding["result"]["databaseMutated"] is False
    assert binding["result"]["providerCallsExecuted"] == 0
    assert agent1["loadStatus"] == "loaded"
    assert agent1["bindingStatus"] == "verified"


def test_gray_contract_build_never_calls_importlib(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_import(name: str):
        raise AssertionError(f"gray imported business runtime: {name}")

    monkeypatch.setattr(gate.importlib, "import_module", forbidden_import)
    contracts = gate.build_selected_module_contracts(
        ROOT,
        environment="gray",
    )
    assert contracts["verified"] is True, contracts["errors"]
    binding = contracts["moduleContracts"]["agent1_runtime"][
        "activeBindingProbe"
    ]
    assert binding["verificationMode"] == "static_ast"


def test_one_binding_failure_does_not_mark_other_modules_unloaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = gate._execute_active_binding_probe

    def isolated_failure(definition, module_id, **kwargs):
        if module_id == "agent1_runtime":
            return None, ["simulated_agent1_binding_failure"]
        return original(definition, module_id, **kwargs)

    monkeypatch.setattr(gate, "_execute_active_binding_probe", isolated_failure)
    contracts = gate.build_selected_module_contracts(
        ROOT,
        environment="gray",
    )
    assert contracts["verified"] is False
    assert contracts["moduleContracts"]["agent1_runtime"]["loadStatus"] == "invalid"
    assert contracts["moduleContracts"]["agent1_runtime"]["bindingStatus"] == "invalid"
    for module_id in (
        "release_governance",
        "agent1_input_projection",
        "agent2_runtime",
    ):
        module = contracts["moduleContracts"][module_id]
        assert module["loadStatus"] == "loaded"
        assert module["moduleErrors"] == []


def test_gray_static_and_production_dynamic_owner_maps_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = _identity()
    monkeypatch.setattr(gate, "_release_manifest_identity", lambda root: identity)
    gray = gate.build_runtime_receipt_set(
        ROOT,
        environment="gray",
        release_commit=_SOURCE_COMMIT,
        release_hash=_RELEASE_HASH,
        captured_at="2026-07-28T00:00:00Z",
    )
    production = gate.build_runtime_receipt_set(
        ROOT,
        environment="production",
        release_commit=_SOURCE_COMMIT,
        release_hash=_RELEASE_HASH,
        captured_at="2026-07-28T00:00:00Z",
    )
    comparison = gate.compare_gray_and_production(gray, production, ROOT)
    assert comparison["passed"] is True, comparison
    gray_agent1 = next(
        item for item in gray["receipts"] if item["moduleId"] == "agent1_runtime"
    )
    production_agent1 = next(
        item for item in production["receipts"]
        if item["moduleId"] == "agent1_runtime"
    )
    assert gray_agent1["activeBindingProbe"]["verificationMode"] == "static_ast"
    assert production_agent1["activeBindingProbe"]["verificationMode"] == "dynamic_runtime"
    assert gray_agent1["activeBindingOwnerMapHash"] == production_agent1[
        "activeBindingOwnerMapHash"
    ]
    assert gray["moduleContractRootHash"] == production[
        "moduleContractRootHash"
    ]


def test_production_dynamic_probe_detects_callable_owner_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = gate.load_runtime_projection(ROOT)
    definition = projection["modules"]["agent1_runtime"]

    class FakeModule:
        @staticmethod
        def active_agent1_runtime_binding():
            result = dict(definition["activeBindingProbe"]["expectedOwners"])
            result["tokenRuntimeOwner"] = "src.services.wrong_runtime"
            result.update(
                matched=False,
                databaseMutated=False,
                providerCallsExecuted=0,
                secondWorkerCreated=False,
            )
            return result

    monkeypatch.setattr(
        gate.importlib,
        "import_module",
        lambda name: FakeModule,
    )
    record, errors = gate._execute_active_binding_probe(
        definition,
        "agent1_runtime",
        environment="production",
        release_root=ROOT,
        selected_paths=gate._selected_implementation_paths(projection),
    )
    assert record is not None
    assert record["verificationMode"] == "dynamic_runtime"
    assert any("owner_mismatch" in error for error in errors)
    assert "src.services.wrong_runtime" in record["ownerMap"].values()
