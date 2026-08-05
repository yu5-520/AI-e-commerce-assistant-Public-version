from __future__ import annotations

import ast
import json
from pathlib import Path

from tools.registry_compiler.compile_registry import verify_committed_manifest
from tools.registry_compiler.module_contracts import build_module_contracts


ROOT = Path(__file__).resolve().parents[1]
V24_MIGRATION_ID = "REG-MIG-V24-0-FOUNDATION-001"
EXPECTED_RUNNER = (
    "src.services.agent_runtime_hard_interface_v22515_service:"
    "run_agent2_microbatch_hard"
)
ACTIVE_PATHS = {
    "src/services/agent_runtime_hard_interface_v22515_service.py",
    "src/services/agent2_runtime_v22515_service.py",
    "src/services/agent_runtime_contract_v225_service.py",
    "src/services/agent2_action_draft_core_v225_service.py",
    "src/services/agent2_hash_proof_bridge_v22515_service.py",
    "src/services/hash_directed_artifact_runtime_v2259_service.py",
}
FORBIDDEN_PRIMARY_PATHS = {
    "src/services/agent_runtime_hard_interface_v230_service.py",
    "src/services/agent_input_contract_v230_service.py",
    "src/services/agent_token_runtime_v230_service.py",
    "src/services/agent2_runtime_resilience_v2143_service.py",
    "src/services/agent2_action_plan_core_v20_service.py",
    "src/services/agent_runtime_contract_v2141_service.py",
    "src/services/pipeline_action_microbatch_v205_service.py",
}


def _read(path: str) -> dict:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _module(document: dict, module_id: str) -> dict:
    return next(item for item in document["modules"] if item["moduleId"] == module_id)


def _v24_migration() -> dict:
    migrations = _read("contracts/registry/migrations.json")["migrations"]
    return next(item for item in migrations if item["migrationId"] == V24_MIGRATION_ID)


def test_registry_and_runtime_projection_point_to_active_agent2_owner() -> None:
    registry = _read("contracts/registry/modules.json")
    projection = _read("config/v23_registry_runtime.json")
    manifest = _read("contracts/registry/registry-manifest.json")
    migration = _v24_migration()

    assert _module(registry, "agent2_runtime")["runner"] == EXPECTED_RUNNER
    agent2 = projection["modules"]["agent2_runtime"]
    assert agent2["runner"] == EXPECTED_RUNNER
    assert set(agent2["implementationPaths"]) == ACTIVE_PATHS
    assert not (set(agent2["implementationPaths"]) & FORBIDDEN_PRIMARY_PATHS)
    assert projection["registryRootHash"] == migration["baseRegistryRootHash"]
    assert manifest["registryRootHash"] != projection["registryRootHash"]
    assert migration["runtimeBehaviorChanged"] is False


def test_active_facade_aliases_agent2_to_v22515_runtime_without_import_side_effects() -> None:
    path = ROOT / "src/services/agent_runtime_hard_interface_v22515_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    imported = False
    aliased = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == (
            "src.services.agent2_runtime_v22515_service"
        ):
            imported = any(
                alias.name == "run_agent2_draft_microbatch_hard"
                for alias in node.names
            )
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "run_agent2_microbatch_hard" in targets:
                aliased = (
                    isinstance(node.value, ast.Name)
                    and node.value.id == "run_agent2_draft_microbatch_hard"
                )

    assert imported is True
    assert aliased is True


def test_agent2_module_contract_hashes_the_active_implementation_chain() -> None:
    contracts = build_module_contracts(ROOT)
    contract = contracts["moduleContracts"]["agent2_runtime"]
    paths = set(contract["implementationContentHashes"])

    assert contract["runnerPath"] == (
        "src/services/agent_runtime_hard_interface_v22515_service.py"
    )
    assert ACTIVE_PATHS <= paths
    assert not (paths & FORBIDDEN_PRIMARY_PATHS)
    assert all(contract["implementationContentHashes"][path] for path in ACTIVE_PATHS)


def test_registry_manifest_is_deterministic_after_owner_rotation() -> None:
    verification = verify_committed_manifest(ROOT)
    assert verification["verified"] is True
    assert verification["committedRegistryRootHash"] != (
        "sha256:d604a0842c14f04d1f3963afa1fe3a1197519d72350021c6301c2f6b153323c5"
    )


def test_release_compatibility_ingress_is_not_the_agent2_registry_owner() -> None:
    policy = _read("release/release-policy.json")
    registry = _read("contracts/registry/modules.json")
    compatibility_entrypoint = (
        "src.services.agent_runtime_hard_interface_v230_service:"
        "run_agent_pipeline_tick_hard"
    )

    assert compatibility_entrypoint in set(policy["allowedEntrypoints"])
    assert _module(registry, "agent2_runtime")["runner"] == EXPECTED_RUNNER
    assert _module(registry, "agent2_runtime")["runner"] != compatibility_entrypoint
