from __future__ import annotations

import json
from pathlib import Path

from src.services.registry_runtime_receipt_v23_service import (
    build_selected_module_contracts,
    load_runtime_projection,
)
from tools.registry_compiler.change_manifest import (
    load_change_manifest,
    validate_change_manifest,
)
from tools.registry_compiler.compile_registry import (
    audit_registry,
    verify_committed_manifest,
)
from tools.registry_compiler.completeness_report import build_completeness_report


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts" / "registry"
CHANGE = ROOT / "contracts" / "changes" / "CHG-V23-FINAL-001.json"
FINAL_ROOT = "sha256:d604a0842c14f04d1f3963afa1fe3a1197519d72350021c6301c2f6b153323c5"
AGENT2_RUNNER = (
    "src.services.agent_runtime_hard_interface_v230_service:"
    "run_agent2_microbatch_hard"
)
AGENT2_PROJECTION_RUNNER = (
    "src.services.agent_input_transport_v230_service:ensure_agent2_input_ref"
)


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_v23_final_registry_root_is_valid_and_committed() -> None:
    audit = audit_registry(ROOT)
    verification = verify_committed_manifest(ROOT)
    assert audit["verified"] is True
    assert audit["errors"] == []
    assert audit["registryRootHash"] == FINAL_ROOT
    assert verification["verified"] is True
    assert verification["expectedRegistryRootHash"] == FINAL_ROOT
    assert verification["committedRegistryRootHash"] == FINAL_ROOT


def test_v23_final_registers_live_agent2_fields_and_schema() -> None:
    fields = {
        item["fieldId"]: item
        for item in _object(REGISTRY / "fields.json")["fields"]
    }
    schemas = {
        item["schemaId"]: item
        for item in _object(REGISTRY / "schemas.json")["schemas"]
    }
    assert fields["agent1.decision_ir"]["canonicalPath"] == "agent1DecisionIR"
    assert fields["agent1.decision_ir"]["ownerModule"] == "agent1_runtime"
    assert fields["action_pack.matrix_dispatch"]["canonicalPath"] == "matrixDispatch"
    assert fields["action_pack.matrix_dispatch"]["ownerModule"] == "action_pack"
    agent2_schema = schemas["agent_input.agent2.v1"]
    assert agent2_schema["ownerModule"] == "agent2_input_projection"
    assert "action_pack.matrix_dispatch" in agent2_schema["requiredFields"]
    assert "agent1.decision_ir" in agent2_schema["optionalFields"]


def test_v23_final_agent2_registry_uses_unique_live_runner() -> None:
    modules = {
        item["moduleId"]: item
        for item in _object(REGISTRY / "modules.json")["modules"]
    }
    projection = modules["agent2_input_projection"]
    runtime = modules["agent2_runtime"]
    assert projection["runner"] == AGENT2_PROJECTION_RUNNER
    assert projection["outputSchemas"] == ["agent_input.agent2.v1"]
    assert runtime["runner"] == AGENT2_RUNNER
    assert runtime["inputSchemas"] == ["agent_input.agent2.v1"]
    assert "agent1.decision_ir" in runtime["reads"]
    assert "action_pack.matrix_dispatch" in runtime["reads"]
    assert "agent2_runtime_v22521_service:run_agent2_draft_microbatch_hard" not in json.dumps(
        modules,
        sort_keys=True,
    )


def test_v23_final_sealed_projection_promotes_agent2() -> None:
    projection = load_runtime_projection(ROOT)
    manifest = _object(REGISTRY / "registry-manifest.json")
    assert projection["releaseVersion"] == "23.0.0"
    assert projection["registryRootHash"] == manifest["registryRootHash"] == FINAL_ROOT
    assert projection["requiredModules"] == [
        "agent1_runtime",
        "agent2_runtime",
        "release_governance",
    ]
    assert projection["modules"]["agent2_runtime"]["runner"] == AGENT2_RUNNER
    assert projection["modules"]["agent2_runtime"]["schemaIds"] == [
        "agent_input.agent2.v1"
    ]
    assert projection["rules"]["deploymentMustFailClosed"] is True


def test_v23_final_selected_module_receipts_resolve_agent2() -> None:
    contracts = build_selected_module_contracts(ROOT)
    assert contracts["verified"] is True
    assert contracts["errors"] == []
    assert contracts["registryRootHash"] == FINAL_ROOT
    assert contracts["requiredModules"] == [
        "agent1_runtime",
        "agent2_runtime",
        "release_governance",
    ]
    agent2 = contracts["moduleContracts"]["agent2_runtime"]
    assert agent2["definition"]["runner"] == AGENT2_RUNNER
    assert agent2["runnerFileExists"] is True
    assert agent2["runnerSymbolExists"] is True
    assert agent2["moduleContractHash"].startswith("sha256:")
    hashes = agent2["implementationContentHashes"]
    assert hashes
    assert all(value and value.startswith("sha256:") for value in hashes.values())


def test_v23_final_change_completeness_passes() -> None:
    manifest = load_change_manifest(CHANGE)
    validation = validate_change_manifest(manifest, ROOT)
    report = build_completeness_report(manifest, ROOT)
    assert validation["valid"] is True
    assert validation["manifest"]["approval"]["status"] == "APPROVED"
    assert report["softGateStatus"] == "PASS"
    assert report["softGatePassed"] is True
    assert report["missingRequiredChanges"] == []
    assert report["unexpectedChangedModules"] == []
    assert report["unverifiedAffectedModules"] == []
    assert report["approvedNoCodeOutsideImpact"] == []


def test_v23_final_source_gate_policy_matches_runtime_projection() -> None:
    policy = _object(ROOT / "contracts" / "receipts" / "rc1-gate-policy.json")
    projection = load_runtime_projection(ROOT)
    assert policy["releaseVersion"] == "23.0.0"
    assert sorted(policy["requiredReceiptModules"]) == projection["requiredModules"]
    assert policy["deferredRunnerBindings"] == {}
    assert policy["rules"]["deploymentMustFailClosed"] is True
