"""Apply and verify the contest logical-isolation overlay without moving files."""
from __future__ import annotations

import importlib.util
import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Set

REGISTRY_FILENAMES = (
    "fields.json",
    "interfaces.json",
    "migrations.json",
    "modules.json",
    "ownership.json",
    "schemas.json",
    "stations.json",
    "tombstones.json",
)


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _closure(seeds: Iterable[str], adjacency: Mapping[str, Iterable[str]]) -> Set[str]:
    visited: Set[str] = set()
    queue = deque(str(value) for value in seeds if str(value))
    while queue:
        item = queue.popleft()
        if item in visited:
            continue
        visited.add(item)
        for adjacent in adjacency.get(item) or []:
            value = str(adjacent)
            if value and value not in visited:
                queue.append(value)
    return visited


def run(root: Path, output_dir: Path) -> Dict[str, Any]:
    root = root.resolve()
    policy = _read(root / "governance/contest/isolation-policy.json")
    transaction = _read(output_dir / "module-isolation-transaction.json")
    simulation = _read(output_dir / "module-isolation-simulation.json")
    field_revalidation = _read(output_dir / "field-compat-registry-revalidation.json")
    selection = _read(output_dir / "contest-selection-manifest.json")
    runtime = _read(root / "config/v23_registry_runtime.json")
    documents = {name: _read(root / "contracts/registry" / name) for name in REGISTRY_FILENAMES}
    z_compile = _load(
        root / "governance/contest/z-interface/Z1.0.5/tools/registry_compiler/compile_registry.py",
        "_logical_isolation_z_compile",
    )

    isolated = sorted(str(value) for value in policy.get("isolatedModuleIds") or [])
    expected = sorted(str(value) for value in transaction.get("isolateModuleIds") or [])
    if isolated != expected or len(isolated) != 7:
        raise RuntimeError(f"ISOLATE_SET_MISMATCH:{isolated}:{expected}")

    modules = {
        str(item.get("moduleId")): item
        for item in documents["modules.json"].get("modules") or []
        if isinstance(item, dict) and str(item.get("moduleId") or "")
    }
    migrations = {
        str(item.get("migrationId")): item
        for item in documents["migrations.json"].get("migrations") or []
        if isinstance(item, dict)
    }
    migration = migrations.get("REG-MIG-CONTEST-REGISTERED-ONLY-ISOLATION-001")
    if not isinstance(migration, dict):
        raise RuntimeError("LOGICAL_ISOLATION_MIGRATION_NOT_REGISTERED")

    manifest_path = root / "contracts/registry/registry-manifest.json"
    previous_manifest = _read(manifest_path)
    registry_files = [
        {"path": f"contracts/registry/{name}", "contentHash": z_compile.sha256_value(documents[name])}
        for name in REGISTRY_FILENAMES
    ]
    manifest_material = {
        "schema": "registry.manifest.v1",
        "version": str(previous_manifest.get("version") or "23.0.0-alpha.1"),
        "mode": str(previous_manifest.get("mode") or "report_only"),
        "registryFiles": registry_files,
    }
    manifest = {
        **manifest_material,
        "generatedBy": "tools.registry_compiler.compile_registry",
        "registryRootHash": z_compile.sha256_value(manifest_material),
    }
    _write(manifest_path, manifest)

    import tools.registry_compiler.compile_registry as product_compile
    product_compile.load_registry_documents = lambda _root=None: documents
    product_compile.sha256_value = z_compile.sha256_value
    from tools.registry_compiler.registry_graph import build_dependency_graph
    from tools.registry_compiler.repository_audit import scan_repository

    graph = build_dependency_graph(root)
    audit = scan_repository(root)
    runtime_modules = dict(runtime.get("modules") or {})
    required_runtime = set(str(value) for value in runtime.get("requiredModules") or [])

    module_results = []
    for module_id in isolated:
        module = dict(modules.get(module_id) or {})
        audit_item = dict((audit.get("moduleAudits") or {}).get(module_id) or {})
        interface_ids = sorted(
            key for key, owners in (graph.get("interfaces") or {}).items()
            if module_id in (owners or [])
        )
        station_ids = sorted(
            key for key, owners in (graph.get("stations") or {}).items()
            if module_id in (owners or [])
        )
        upstream = sorted(str(value) for value in (graph.get("upstream") or {}).get(module_id) or [])
        downstream = sorted(str(value) for value in (graph.get("downstream") or {}).get(module_id) or [])
        result = {
            "moduleId": module_id,
            "registryStatus": module.get("status"),
            "activationState": module.get("activationState"),
            "runtimeProjectionPresent": module_id in runtime_modules,
            "requiredRuntimeModule": module_id in required_runtime,
            "interfaceIds": interface_ids,
            "stationIds": station_ids,
            "upstream": upstream,
            "downstream": downstream,
            "dispatchEvidenceCount": int(audit_item.get("dispatchEvidenceCount") or 0),
            "runnerFileExists": audit_item.get("runnerFileExists"),
            "runnerSymbolExists": audit_item.get("runnerSymbolExists"),
        }
        result["verified"] = (
            (result["registryStatus"] == "REGISTERED_ONLY" or result["activationState"] == "REGISTERED_ONLY")
            and not result["runtimeProjectionPresent"]
            and not result["requiredRuntimeModule"]
            and not interface_ids
            and not station_ids
            and not upstream
            and not downstream
            and result["dispatchEvidenceCount"] == 0
        )
        module_results.append(result)

    keep_modules = set(selection.get("safeKeepModuleIds") or [])
    if not keep_modules:
        classifications = selection.get("classifications") or {}
        keep_modules = set(classifications.get("KEEP_CORE") or []) | set(classifications.get("KEEP_SUPPORT") or [])
    isolated_in_keep = sorted(set(isolated) & keep_modules)
    simulation_paths = sorted(str(value) for value in policy.get("physicalPathPolicy", {}).get("simulationExcludeCandidates") or [])
    physical_paths_exist = all((root / path).is_file() for path in simulation_paths)
    simulation_assertions = dict(simulation.get("assertions") or {})

    assertions = {
        "policyActive": policy.get("state") == "LOGICAL_ISOLATION_ACTIVE" and policy.get("mode") == "fail_closed",
        "migrationActive": migration.get("activationState") == "LOGICAL_ISOLATION_ACTIVE",
        "moduleSetVerified": all(bool(item.get("verified")) for item in module_results),
        "isolatedModulesOutsideKeep": not isolated_in_keep,
        "graphHashStable": graph.get("graphHash") == field_revalidation.get("graphHash") == policy.get("graphHash"),
        "selectionHashStable": selection.get("selectionHash") == policy.get("selectionHash"),
        "runnerDriftCountZero": int(audit.get("summary", {}).get("runnerDriftCount") or 0) == 0,
        "simulationPreviouslyVerified": all(bool(value) for value in simulation_assertions.values()),
        "physicalPathsPreserved": physical_paths_exist,
        "sourceRegistrationsPreserved": migration.get("sourceModuleRegistrationPreserved") is True,
    }
    if not all(assertions.values()):
        raise RuntimeError("LOGICAL_ISOLATION_ASSERTION_FAILED:" + json.dumps(assertions, sort_keys=True))

    material = {
        "schema": "contest.logical_isolation_application.v1",
        "state": "LOGICAL_ISOLATION_APPLIED",
        "migrationId": migration.get("migrationId"),
        "policyPath": "governance/contest/isolation-policy.json",
        "policyState": policy.get("state"),
        "previousRegistryRootHash": previous_manifest.get("registryRootHash"),
        "registryRootHash": manifest.get("registryRootHash"),
        "graphHash": graph.get("graphHash"),
        "selectionHash": selection.get("selectionHash"),
        "repositoryScanHash": audit.get("repositoryScanHash"),
        "isolatedModuleIds": isolated,
        "moduleResults": module_results,
        "simulationExcludePaths": simulation_paths,
        "assertions": assertions,
        "nextState": "PRUNE_CANDIDATE_COMPILATION_READY",
        "physicalFilesMoved": 0,
        "physicalDeletionExecuted": False,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "mainMutated": False,
        "promotionExecuted": False,
        "publicReleaseExecuted": False,
    }
    report = {**material, "applicationHash": z_compile.sha256_value(material)}
    receipt_material = {
        "schema": "contest.logical_isolation_application_receipt.v1",
        "state": report["state"],
        "applicationHash": report["applicationHash"],
        "registryRootHash": report["registryRootHash"],
        "graphHash": report["graphHash"],
        "selectionHash": report["selectionHash"],
        "isolatedModuleCount": len(isolated),
        "states": [
            "ISOLATION_POLICY_ACTIVE",
            "REGISTERED_ONLY_IDENTITIES_PRESERVED",
            "RUNTIME_PROJECTION_EXCLUDED",
            "INTERFACE_AND_STATION_EXCLUDED",
            "GRAPH_EDGES_EXCLUDED",
            "ACTIVE_DISPATCH_EXCLUDED",
            "LOGICAL_ISOLATION_APPLIED",
            "PRUNE_CANDIDATE_COMPILATION_READY",
        ],
        "physicalFilesMoved": 0,
        "physicalDeletionExecuted": False,
        "mainMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }
    receipt = {**receipt_material, "receiptHash": z_compile.sha256_value(receipt_material)}
    _write(output_dir / "module-isolation-application.json", report)
    _write(output_dir / "module-isolation-application-receipt.json", receipt)
    _write(output_dir / "repository-audit-post-isolation.json", audit)
    return {
        "state": report["state"],
        "registryRootHash": report["registryRootHash"],
        "graphHash": report["graphHash"],
        "selectionHash": report["selectionHash"],
        "applicationHash": report["applicationHash"],
        "receiptHash": receipt["receiptHash"],
        "isolatedModuleCount": len(isolated),
        "nextState": report["nextState"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = Path.cwd().resolve()
    result = run(root, root / "governance/contest/generated")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
