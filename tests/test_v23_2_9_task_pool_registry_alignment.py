from __future__ import annotations

import json
from pathlib import Path

from tools.registry_compiler.compile_registry import verify_committed_manifest

ROOT = Path(__file__).resolve().parents[1]
V24_MIGRATION_ID = "REG-MIG-V24-0-FOUNDATION-001"


def _read(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _module(document: dict, module_id: str) -> dict:
    return next(item for item in document["modules"] if item["moduleId"] == module_id)


def _station(document: dict, station_id: str) -> dict:
    return next(item for item in document["stations"] if item["stationId"] == station_id)


def _v24_migration() -> dict:
    migrations = _read("contracts/registry/migrations.json")["migrations"]
    return next(item for item in migrations if item["migrationId"] == V24_MIGRATION_ID)


def test_registry_points_to_active_v225_task_mapping_and_pool() -> None:
    modules = _read("contracts/registry/modules.json")
    mapping = _module(modules, "task_mapping")
    pool = _module(modules, "task_pool")
    compiler = _module(modules, "registry_compiler")

    assert mapping["runner"].endswith(":run_task_mapping_microbatch_v225")
    assert mapping["activeInputContract"] == "agent3_sop_ready_to_taskMappingRef"
    assert pool["runner"].endswith(":run_task_pool_admission_microbatch_v225")
    assert pool["activeInputContract"] == "taskMappingRef_only"
    assert compiler["registryMigrationRole"] == "owns_deterministic_registry_manifest"


def test_selected_runtime_projection_hashes_exact_task_modules() -> None:
    runtime = _read("config/v23_registry_runtime.json")
    mapping = runtime["modules"]["task_mapping"]
    pool = runtime["modules"]["task_pool"]

    assert {"task_mapping", "task_pool"}.issubset(set(runtime["requiredModules"]))
    assert mapping["runner"].endswith(":run_task_mapping_microbatch_v225")
    assert "src/services/pipeline_task_mapping_v225_service.py" in mapping["implementationPaths"]
    assert pool["runner"].endswith(":run_task_pool_admission_microbatch_v225")
    assert "src/services/task_pool_admission_core_v20_service.py" in pool["implementationPaths"]
    assert runtime["taskPoolRegistryAlignmentVersion"] == "23.2.9"


def test_station_stage_and_manifest_are_self_consistent() -> None:
    stations = _read("contracts/registry/stations.json")
    runtime = _read("config/v23_registry_runtime.json")
    manifest = _read("contracts/registry/registry-manifest.json")
    migration = _v24_migration()

    assert _station(stations, "task_mapping_agent_station")["stage"] == "task_mapped"
    assert _station(stations, "task_pool_admission_station")["stage"] == "task_admitted"
    assert runtime["registryRootHash"] == migration["baseRegistryRootHash"]
    assert manifest["registryRootHash"] != runtime["registryRootHash"]
    assert migration["activeStationGraphChanged"] is False
    assert verify_committed_manifest(ROOT)["verified"] is True


def test_registry_alignment_is_static_only() -> None:
    requirement = _read(
        "contracts/requirements/REQ-V23-2-TASK-POOL-REGISTRY-ALIGNMENT-001.json"
    )
    assert "任何 src/services 业务文件" in requirement["prohibitedChanges"]
    assert requirement["productCapabilityHints"] == [
        "task_generation_pool",
        "release_self_update_governance",
    ]
