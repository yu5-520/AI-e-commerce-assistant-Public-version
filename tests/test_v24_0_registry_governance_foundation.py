from __future__ import annotations

import importlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "contracts" / "registry"
APPROVAL_PATH = (
    ROOT
    / "contracts"
    / "approvals"
    / "REQ-V24-0-REGISTRY-GOVERNANCE-FOUNDATION-001.json"
)
BASE_REGISTRY_ROOT = "sha256:c6308a05333fadc9467413cb7a68099d2e6958bceca0b265b764a4407b4eb0ac"

PLANNED_MODULES = {
    "operating_plan_compiler",
    "action_node_transport",
    "execution_resource_orchestrator",
    "node_authorization",
    "task_blueprint_compiler",
    "stage_lifecycle",
    "stage_frontend_projection",
}

PLANNED_RUNNERS = {
    "operating_plan_compiler": (
        "tools.registry_compiler.v24_identity_catalog:operating_plan_compiler_identity"
    ),
    "action_node_transport": (
        "tools.registry_compiler.v24_identity_catalog:action_node_transport_identity"
    ),
    "execution_resource_orchestrator": (
        "tools.registry_compiler.v24_identity_catalog:execution_resource_orchestrator_identity"
    ),
    "node_authorization": (
        "tools.registry_compiler.v24_identity_catalog:node_authorization_identity"
    ),
    "task_blueprint_compiler": (
        "tools.registry_compiler.v24_task_blueprint_compiler:task_blueprint_compiler_identity"
    ),
    "stage_lifecycle": (
        "tools.registry_compiler.v24_identity_catalog:stage_lifecycle_identity"
    ),
    "stage_frontend_projection": (
        "tools.registry_compiler.v24_identity_catalog:stage_frontend_projection_identity"
    ),
}

MIGRATION_PATHS = {
    "contracts/registry/fields.json",
    "contracts/registry/migrations.json",
    "contracts/registry/modules.json",
    "contracts/registry/ownership.json",
    "contracts/registry/registry-manifest.json",
    "contracts/registry/schemas.json",
    "tools/registry_compiler/v24_identity_catalog.py",
}

PLANNED_FIELDS = {
    "entity.plan_id",
    "entity.stage_id",
    "entity.action_node_id",
    "entity.parent_task_id",
    "agent1.operating_plan_ir",
    "agent1.primary_action_node",
    "agent1.supporting_action_nodes",
    "agent1.plan_dependency_edges",
    "plan.action_node_contract",
    "plan.stage_graph",
    "plan.dependency_edges",
    "plan.decision_branches",
    "plan.current_stage_id",
    "plan.blueprint_status",
    "execution.operating_plan_ir_ref",
    "execution.plan_graph_ref",
    "execution.action_node_contract_ref",
    "execution.agent2_node_draft_ref",
    "execution.agent3_node_sop_ref",
    "execution.task_execution_blueprint_ref",
    "execution.stage_lifecycle_snapshot_ref",
    "execution.resource_context",
    "execution.node_authorization",
    "agent2.action_node_draft",
    "agent2.action_node_execution_proof",
    "agent3.action_node_sop",
    "agent3.action_node_execution_proof",
    "task.current_stage_id",
    "task.stage_status_map",
    "task.node_status_map",
    "task.blocked_stage_ids",
    "task.awaiting_approval_stage_ids",
    "task.completed_stage_ids",
    "frontend.stage_navigation_hash",
    "frontend.active_stage_content_hash",
    "frontend.blueprint_manifest_hash",
}

PLANNED_SCHEMAS = {
    "agent1.operating_plan_ir.v24",
    "plan.action_node_contract.v24",
    "transport.action_node_artifacts.v24",
    "execution.resource_context.v24",
    "execution.node_authorization.v24",
    "agent_output.agent2_action_node.v24",
    "agent_output.agent3_action_node_sop.v24",
    "plan.stage_graph.v24",
    "task.execution_blueprint.v24",
    "task.stage_lifecycle_snapshot.v24",
    "frontend_view.task_stage_navigation.v24",
}


def _load(name: str) -> dict:
    return json.loads((REGISTRY / name).read_text(encoding="utf-8"))


def test_v24_foundation_registers_identity_without_active_runtime_edges() -> None:
    modules = {item["moduleId"]: item for item in _load("modules.json")["modules"]}

    assert PLANNED_MODULES <= set(modules)
    for module_id in PLANNED_MODULES:
        module = modules[module_id]
        assert module["status"] == "REGISTERED_ONLY"
        assert module["activationState"] == "REGISTERED_ONLY"
        assert module["runtimeBindingEnabled"] is False
        assert module["upstream"] == []
        assert module["downstream"] == []
        assert module["runner"] == PLANNED_RUNNERS[module_id]

    active_modules = [
        item for item in modules.values()
        if item["moduleId"] not in PLANNED_MODULES
    ]
    for module in active_modules:
        assert PLANNED_MODULES.isdisjoint(module.get("upstream") or [])
        assert PLANNED_MODULES.isdisjoint(module.get("downstream") or [])


def test_v24_foundation_fields_and_schemas_are_registered_only() -> None:
    fields = {item["fieldId"]: item for item in _load("fields.json")["fields"]}
    schemas = {item["schemaId"]: item for item in _load("schemas.json")["schemas"]}

    assert PLANNED_FIELDS <= set(fields)
    assert PLANNED_SCHEMAS <= set(schemas)

    for field_id in PLANNED_FIELDS:
        field = fields[field_id]
        assert field["activationState"] == "REGISTERED_ONLY"
        assert field["runtimeBindingEnabled"] is False
        assert field["status"] == "ACTIVE"

    for schema_id in PLANNED_SCHEMAS:
        schema = schemas[schema_id]
        assert schema["activationState"] == "REGISTERED_ONLY"
        assert schema["runtimeBindingEnabled"] is False
        assert schema["status"] == "REGISTERED_ONLY"


def test_v24_foundation_identity_runners_are_non_mutating() -> None:
    for module_id, runner in PLANNED_RUNNERS.items():
        module_name, _, symbol = runner.partition(":")
        module = importlib.import_module(module_name)
        function = getattr(module, symbol)
        result = function()
        assert result["moduleId"] == module_id
        assert result["activationState"] == "REGISTERED_ONLY"
        assert result["runtimeBindingEnabled"] is False
        assert result["businessRuntimeMutated"] is False
        assert result["databaseMutated"] is False
        assert result["providerCallsExecuted"] == 0


def test_v24_foundation_migration_is_governance_only() -> None:
    migrations = {
        item["migrationId"]: item
        for item in _load("migrations.json")["migrations"]
    }
    migration = migrations["REG-MIG-V24-0-FOUNDATION-001"]

    assert migration["activationState"] == "REGISTERED_ONLY"
    assert migration["runtimeBehaviorChanged"] is False
    assert migration["businessDataMutated"] is False
    assert migration["providerCallsExecuted"] == 0
    assert migration["activeStationGraphChanged"] is False
    assert migration["activeInterfaceContractChanged"] is False


def test_v24_foundation_approval_is_base_bound_and_migration_scoped() -> None:
    approval = json.loads(APPROVAL_PATH.read_text(encoding="utf-8"))
    plan = approval["registryMigrationPlan"]

    assert approval["approvedRegistryRootHash"] == BASE_REGISTRY_ROOT
    assert approval["approvedRequirementIrHash"] == (
        "sha256:d6c4f946209b89ebb6c76ec2b18a3bef9a2674a53b47c2d7de508602c7628e80"
    )
    assert approval["approvedImpactHash"] == (
        "sha256:9318835b016a0f96c0170b9171a9d0413fbfa1cafa81dccae2e80a85eb145d22"
    )
    assert approval["approvedImpactBundleHash"] == (
        "sha256:15650979862539faa67ec5447d17980cfc6fcf5c7c3818b1ed42a71c0515d6f9"
    )
    assert plan["baseRegistryRootHash"] == BASE_REGISTRY_ROOT
    assert set(plan["allowedRegistryPaths"]) == MIGRATION_PATHS
    assert set(plan["targetModules"]) == PLANNED_MODULES
    assert plan["migrationPlanHash"] == (
        "sha256:78def52d9c7b7b33138cad9fdf21b6757ab81ddbda8911e2c4b07cc6bc120323"
    )
