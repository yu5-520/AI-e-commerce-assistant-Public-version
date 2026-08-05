"""Reseal Registry and lineage after the contest field compatibility migration."""
from __future__ import annotations

import importlib.util
import json
import tempfile
from collections import Counter, deque
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


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_LOAD_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _closure(seeds: Iterable[str], adjacency: Mapping[str, Iterable[str]]) -> Set[str]:
    visited: Set[str] = set()
    queue = deque(str(item) for item in seeds if str(item))
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


def _normalize_document(document: Mapping[str, Any], mapping: Mapping[str, Any]) -> Dict[str, Any]:
    source_collection = str(mapping.get("sourceCollection") or "")
    target_collection = str(mapping.get("targetCollection") or "")
    source_identity = str(mapping.get("sourceIdentity") or "")
    target_identity = str(mapping.get("targetIdentity") or "")
    mode = str(mapping.get("mappingMode") or "")
    prefix = str(mapping.get("identityPrefix") or "")
    records = document.get(source_collection)
    if not isinstance(records, list):
        raise RuntimeError(f"SOURCE_COLLECTION_REQUIRED:{mapping.get('path')}:{source_collection}")
    normalized = []
    seen: Set[str] = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise RuntimeError(f"SOURCE_RECORD_OBJECT_REQUIRED:{mapping.get('path')}")
        source_id = str(raw.get(source_identity) or "").strip()
        if not source_id:
            raise RuntimeError(f"SOURCE_ID_REQUIRED:{mapping.get('path')}:{source_identity}")
        if mode == "PASSTHROUGH":
            target_id = source_id
            record = dict(raw)
        elif mode == "COLLECTION_AND_IDENTITY_ADAPTER":
            target_id = prefix + source_id
            record = {**raw, target_identity: target_id}
        else:
            raise RuntimeError(f"MAPPING_MODE_UNSUPPORTED:{mode}")
        if target_id in seen:
            raise RuntimeError(f"TARGET_ID_DUPLICATE:{mapping.get('path')}:{target_id}")
        seen.add(target_id)
        normalized.append(record)
    result = {key: value for key, value in document.items() if key != source_collection}
    result[target_collection] = normalized
    return result


def _module_classifications(graph: Mapping[str, Any], request: Mapping[str, Any], modules: Mapping[str, Any]) -> Dict[str, list[str]]:
    entry = set(str(value) for value in request.get("entryModuleIds") or [])
    terminal = set(str(value) for value in request.get("terminalModuleIds") or [])
    required = set(str(value) for value in request.get("requiredModuleIds") or [])
    support_seed = set(str(value) for value in request.get("supportModuleIds") or [])
    interface_modules: Set[str] = set()
    for interface_id in request.get("requiredInterfaceIds") or []:
        interface_modules.update(str(value) for value in (graph.get("interfaces") or {}).get(interface_id) or [])
    station_modules: Set[str] = set()
    for station_id in request.get("requiredStationIds") or []:
        station_modules.update(str(value) for value in (graph.get("stations") or {}).get(station_id) or [])
    forward = _closure(entry, graph.get("downstream") or {})
    reverse = _closure(terminal, graph.get("upstream") or {})
    core = (forward & reverse) | (required - support_seed) | station_modules | (interface_modules - support_seed)
    support = support_seed | (_closure(core, graph.get("upstream") or {}) - core)
    result: MutableMapping[str, list[str]] = {
        "KEEP_CORE": [],
        "KEEP_SUPPORT": [],
        "ISOLATE": [],
        "REMOVE_CANDIDATE": [],
        "REVIEW_REQUIRED": [],
    }
    for module_id, raw in sorted(modules.items()):
        module = dict(raw or {})
        if module_id in core:
            result["KEEP_CORE"].append(module_id)
        elif module_id in support:
            result["KEEP_SUPPORT"].append(module_id)
        elif str(module.get("status") or "") == "REGISTERED_ONLY" or str(module.get("activationState") or "") == "REGISTERED_ONLY":
            result["ISOLATE"].append(module_id)
        elif str(module.get("status") or "") == "AUDIT_ONLY":
            result["ISOLATE"].append(module_id)
        else:
            result["REVIEW_REQUIRED"].append(module_id)
    return {key: sorted(value) for key, value in result.items()}


def run(root: Path, output_dir: Path) -> Dict[str, Any]:
    root = root.resolve()
    interface_path = root / "governance/contest/z-interface/Z1.0.5/tools/registry_compiler/compile_registry.py"
    z_compile = _load_module(interface_path, "_field_compat_z_compile")
    documents = {name: _read(root / "contracts/registry" / name) for name in REGISTRY_FILENAMES}

    current_manifest_path = root / "contracts/registry/registry-manifest.json"
    previous_manifest = _read(current_manifest_path)
    registry_files = [
        {
            "path": f"contracts/registry/{name}",
            "contentHash": z_compile.sha256_value(documents[name]),
        }
        for name in REGISTRY_FILENAMES
    ]
    manifest_material = {
        "schema": "registry.manifest.v1",
        "version": str(previous_manifest.get("version") or "23.0.0-alpha.1"),
        "mode": str(previous_manifest.get("mode") or "report_only"),
        "registryFiles": registry_files,
    }
    new_manifest = {
        **manifest_material,
        "generatedBy": "tools.registry_compiler.compile_registry",
        "registryRootHash": z_compile.sha256_value(manifest_material),
    }
    _write(current_manifest_path, new_manifest)

    import tools.registry_compiler.compile_registry as product_compile

    product_compile.load_registry_documents = lambda _root=None: documents
    product_compile.sha256_value = z_compile.sha256_value
    from tools.registry_compiler.registry_graph import build_dependency_graph
    from tools.registry_compiler.repository_audit import scan_repository

    graph = build_dependency_graph(root)
    audit = scan_repository(root)
    request = _read(root / "governance/contest/selection-request.json")
    baseline_selection = _read(output_dir / "contest-selection-manifest.json")
    modules = {
        str(item.get("moduleId")): item
        for item in documents["modules.json"].get("modules") or []
        if isinstance(item, dict) and str(item.get("moduleId") or "")
    }
    classifications = _module_classifications(graph, request, modules)
    expected_classifications = {
        key: sorted(str(value) for value in values)
        for key, values in (baseline_selection.get("classifications") or {}).items()
    }
    classifications_stable = classifications == expected_classifications

    protocol_map = _read(root / "governance/contest/registry-protocol-map.json")
    normalized: Dict[str, Dict[str, Any]] = {}
    for mapping in protocol_map.get("documents") or []:
        if isinstance(mapping, dict):
            filename = str(mapping.get("path") or "")
            normalized[filename] = _normalize_document(documents[filename], mapping)
    with tempfile.TemporaryDirectory(prefix="contest-field-registry-") as temporary:
        temporary_root = Path(temporary)
        registry_dir = temporary_root / "contracts/registry"
        registry_dir.mkdir(parents=True, exist_ok=True)
        for filename, document in normalized.items():
            _write(registry_dir / filename, document)
        normalized_manifest = z_compile.compile_registry(temporary_root, write=False)

    migration = next(
        (
            item
            for item in documents["migrations.json"].get("migrations") or []
            if isinstance(item, dict) and item.get("migrationId") == "REG-MIG-CONTEST-FIELD-COMPAT-001"
        ),
        None,
    )
    if not isinstance(migration, dict):
        raise RuntimeError("FIELD_COMPAT_MIGRATION_NOT_REGISTERED")
    tombstones = {
        str(item.get("legacyPath")): item
        for item in documents["tombstones.json"].get("candidates") or []
        if isinstance(item, dict)
    }
    blocking_paths = ("payload.selectedActionFamilyHint", "payload.creativeTestPlan")
    tombstone_states_verified = all(
        tombstones.get(path, {}).get("status") == "COMPATIBILITY_ACTIVE"
        and tombstones.get(path, {}).get("migrationId") == migration.get("migrationId")
        and tombstones.get(path, {}).get("retirementBlocked") is True
        for path in blocking_paths
    )
    semantic = _read(output_dir / "field-compat-semantic-verification.json")
    apply_receipt = _read(output_dir / "field-compat-apply-receipt.json")

    scope_module = _load_module(
        root / "tools/contest_governance/scope_unregistered_fields.py",
        "_field_compat_scope",
    )
    seeds = scope_module._registered_seeds(baseline_selection)
    closures: Dict[str, Set[str]] = {}
    for classification in ("KEEP_CORE", "KEEP_SUPPORT", "ISOLATE"):
        closure, _origin = scope_module._closure(root, seeds[classification])
        closures[classification] = closure
    legacy_counts: Counter[str] = Counter()
    blocking_counts: Counter[str] = Counter()
    for hit in audit.get("retiredFieldCandidateHits") or []:
        if not isinstance(hit, dict):
            continue
        candidate = dict(hit.get("candidate") or {})
        legacy_path = str(candidate.get("legacyPath") or hit.get("key") or "")
        path = str(hit.get("path") or "")
        scopes = {
            classification
            for classification, paths in closures.items()
            if path in paths
        }
        classification = scope_module._candidate_classification(scopes)
        legacy_counts[classification] += 1
        if legacy_path in blocking_paths:
            blocking_counts[classification] += 1

    assertions = {
        "manifestResealed": new_manifest["registryRootHash"] != previous_manifest.get("registryRootHash"),
        "graphHashStable": graph.get("graphHash") == baseline_selection.get("graphHash"),
        "moduleClassificationsStable": classifications_stable,
        "runnerDriftCountZero": int(audit.get("summary", {}).get("runnerDriftCount") or 0) == 0,
        "migrationRegistered": migration.get("activationState") == "COMPATIBILITY_ACTIVE",
        "tombstoneStatesVerified": tombstone_states_verified,
        "semanticVerificationPassed": semantic.get("state") == "FIELD_COMPATIBILITY_SEMANTICS_VERIFIED" and int(semantic.get("violationCount") or 0) == 0,
        "newWritePolicyVerified": apply_receipt.get("writePolicy") == "NEW_KEY_ONLY",
        "legacyFallbackPolicyVerified": apply_receipt.get("readPolicy") == "NEW_KEY_THEN_LEGACY_KEY",
        "normalizedZCompileVerified": len(normalized_manifest.get("registryFiles") or []) == 8,
    }
    if not all(assertions.values()):
        raise RuntimeError("FIELD_COMPAT_REVALIDATION_FAILED:" + json.dumps(assertions, sort_keys=True))

    material = {
        "schema": "contest.field_compat_registry_revalidation.v1",
        "state": "FIELD_MIGRATION_VERIFIED_COMPATIBILITY_WINDOW_ACTIVE",
        "previousRegistryRootHash": previous_manifest.get("registryRootHash"),
        "registryRootHash": new_manifest["registryRootHash"],
        "normalizedZRegistryRootHash": normalized_manifest.get("registryRootHash"),
        "graphHash": graph.get("graphHash"),
        "repositoryScanHash": audit.get("repositoryScanHash"),
        "selectionHash": baseline_selection.get("selectionHash"),
        "migrationId": migration.get("migrationId"),
        "compatibilityCommit": migration.get("compatibilityCommit"),
        "classifications": classifications,
        "legacyReferenceScopeCounts": dict(sorted(legacy_counts.items())),
        "blockingFieldReferenceScopeCounts": dict(sorted(blocking_counts.items())),
        "semanticCounts": {
            "legacyPairedReads": semantic.get("legacyPairedReads"),
            "legacyCleanupPops": semantic.get("legacyCleanupPops"),
            "modernWrites": semantic.get("modernWrites"),
            "violationCount": semantic.get("violationCount"),
        },
        "assertions": assertions,
        "physicalDeletionAuthorized": False,
        "logicalIsolationAuthorizedNext": True,
        "mainMutated": False,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }
    report = {**material, "revalidationHash": z_compile.sha256_value(material)}
    receipt_material = {
        "schema": "contest.field_compat_registry_revalidation_receipt.v1",
        "state": report["state"],
        "registryRootHash": report["registryRootHash"],
        "graphHash": report["graphHash"],
        "selectionHash": report["selectionHash"],
        "revalidationHash": report["revalidationHash"],
        "states": [
            "MIGRATION_REGISTERED",
            "TOMBSTONES_COMPATIBILITY_ACTIVE",
            "REGISTRY_MANIFEST_RESEALED",
            "LINEAGE_REVERIFIED",
            "RUNNER_DRIFT_ZERO",
            "FIELD_MIGRATION_VERIFIED_COMPATIBILITY_WINDOW_ACTIVE",
        ],
        "physicalDeletionExecuted": False,
        "mainMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }
    receipt = {**receipt_material, "receiptHash": z_compile.sha256_value(receipt_material)}
    _write(output_dir / "field-compat-registry-revalidation.json", report)
    _write(output_dir / "field-compat-registry-revalidation-receipt.json", receipt)
    _write(output_dir / "repository-audit-post-field-compat.json", audit)
    return {
        "state": report["state"],
        "registryRootHash": report["registryRootHash"],
        "graphHash": report["graphHash"],
        "selectionHash": report["selectionHash"],
        "repositoryScanHash": report["repositoryScanHash"],
        "revalidationHash": report["revalidationHash"],
        "receiptHash": receipt["receiptHash"],
        "blockingFieldReferenceScopeCounts": report["blockingFieldReferenceScopeCounts"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = Path.cwd().resolve()
    output = root / "governance/contest/generated"
    result = run(root, output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
