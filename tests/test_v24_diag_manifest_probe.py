from __future__ import annotations

import json
from pathlib import Path

from tools.registry_compiler.compile_registry import verify_committed_manifest
from tools.registry_compiler.v24_task_blueprint_compiler import (
    task_blueprint_compiler_identity,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts" / "registry"
EXPECTED_ROOT = "sha256:e1bbee3ef7b78805ed32a917f304c5585e2cca3195e59856e973a051e2a713b0"
DEDICATED_RUNNER = (
    "tools.registry_compiler.v24_task_blueprint_compiler:task_blueprint_compiler_identity"
)
SHARED_RUNNER_PREFIX = "tools.registry_compiler.v24_identity_catalog:"
OTHER_V24_MODULES = {
    "operating_plan_compiler",
    "action_node_transport",
    "execution_resource_orchestrator",
    "node_authorization",
    "stage_lifecycle",
    "stage_frontend_projection",
}


def _read(name: str) -> dict:
    return json.loads((REGISTRY / name).read_text(encoding="utf-8"))


def test_task_blueprint_compiler_has_one_dedicated_registered_runner() -> None:
    modules = {item["moduleId"]: item for item in _read("modules.json")["modules"]}
    blueprint = modules["task_blueprint_compiler"]

    assert blueprint["runner"] == DEDICATED_RUNNER
    assert blueprint["status"] == "REGISTERED_ONLY"
    assert blueprint["activationState"] == "REGISTERED_ONLY"
    assert blueprint["runtimeBindingEnabled"] is False
    assert blueprint["upstream"] == []
    assert blueprint["downstream"] == []

    for module_id in OTHER_V24_MODULES:
        module = modules[module_id]
        assert module["runner"].startswith(SHARED_RUNNER_PREFIX)
        assert module["runner"] != DEDICATED_RUNNER
        assert module["activationState"] == "REGISTERED_ONLY"
        assert module["runtimeBindingEnabled"] is False
        assert module["upstream"] == []
        assert module["downstream"] == []


def test_dedicated_runner_is_identity_only_and_side_effect_free() -> None:
    identity = task_blueprint_compiler_identity()

    assert identity == {
        "schema": "registry.module_identity.v24",
        "version": "24.0.0",
        "moduleId": "task_blueprint_compiler",
        "activationState": "REGISTERED_ONLY",
        "runtimeBindingEnabled": False,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "compilerVersion": "24.1.0",
        "compilerRunner": DEDICATED_RUNNER,
        "compatibilityModes": ["single_stage_v23_projection"],
        "sideEffectFree": True,
    }


def test_diagnostic_migration_is_registry_only() -> None:
    migrations = {
        item["migrationId"]: item
        for item in _read("migrations.json")["migrations"]
    }
    migration = migrations[
        "REG-MIG-DIAG-V24-1-BLUEPRINT-OWNER-ISOLATION-001"
    ]

    assert migration["requirementId"] == (
        "REQ-DIAG-V24-1-BLUEPRINT-OWNER-ISOLATION-001"
    )
    assert migration["baseRegistryRootHash"] == (
        "sha256:030442f753749479f44ee570a2f7ba1c5ac1cf5f106d954e7f0b439dd04d6414"
    )
    assert migration["plannedModuleIds"] == ["task_blueprint_compiler"]
    assert migration["activationState"] == "REGISTERED_ONLY"
    assert migration["runtimeBehaviorChanged"] is False
    assert migration["businessDataMutated"] is False
    assert migration["providerCallsExecuted"] == 0
    assert migration["activeStationGraphChanged"] is False
    assert migration["activeInterfaceContractChanged"] is False


def test_diagnostic_registry_manifest_is_deterministic() -> None:
    verification = verify_committed_manifest(ROOT)

    assert verification["verified"] is True
    assert verification["committedRegistryRootHash"] == EXPECTED_ROOT
    assert verification["expectedRegistryRootHash"] == EXPECTED_ROOT
