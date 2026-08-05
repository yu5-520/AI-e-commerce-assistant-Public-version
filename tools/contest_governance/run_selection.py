"""Generate the contest chain review from registered identities only.

This runner is report-only. It does not mutate business runtime, databases, provider
state, deployment state, or tracked source files outside governance/contest/generated.
The business graph and repository audit remain the mother repository implementations;
this file only binds them to the product adapter contract and classifies their output.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


GENERATED_DIR = Path("governance/contest/generated")


def _read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_SPEC_UNAVAILABLE:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _records(document: Mapping[str, Any], key: str, identity: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in document.get(key) or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get(identity) or "").strip()
        if item_id:
            result[item_id] = dict(raw)
    return result


def _closure(seeds: Iterable[str], adjacency: Mapping[str, Iterable[str]]) -> Set[str]:
    visited: Set[str] = set()
    queue = deque(str(seed) for seed in seeds if str(seed))
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


def _runner_path(runner: str) -> Optional[str]:
    module_name, separator, _symbol = str(runner or "").partition(":")
    if not separator or not module_name:
        return None
    return "/".join(module_name.split(".")) + ".py"


def _load_adapter_documents(root: Path, sha256_value: Any) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    layout_path = root / ".z" / "adapter" / "registry-layout.json"
    layout = _read_object(layout_path)
    documents: Dict[str, Dict[str, Any]] = {}
    checks: List[Dict[str, Any]] = []

    for record in layout.get("documents") or []:
        if not isinstance(record, dict):
            continue
        relative = str(record.get("path") or "")
        expected = str(record.get("contentHash") or "")
        if not relative:
            continue
        path = root / relative
        document = _read_object(path)
        actual = str(sha256_value(document))
        checks.append(
            {
                "path": relative,
                "expectedContentHash": expected,
                "actualContentHash": actual,
                "matches": actual == expected,
            }
        )
        documents[path.name] = document

    missing = sorted(
        {
            "fields.json",
            "schemas.json",
            "interfaces.json",
            "modules.json",
            "ownership.json",
            "migrations.json",
            "stations.json",
            "tombstones.json",
        }
        - set(documents)
    )
    if missing:
        raise RuntimeError("ADAPTER_DOCUMENTS_MISSING:" + ",".join(missing))

    return documents, {
        "layoutPath": layout_path.relative_to(root).as_posix(),
        "adapterVersion": layout.get("adapterVersion"),
        "activationState": layout.get("activationState"),
        "activationConstraint": layout.get("activationConstraint"),
        "documentChecks": checks,
        "allDocumentsMatch": all(item["matches"] for item in checks),
        "manifest": layout.get("manifest"),
        "observedLayeredRoots": layout.get("observedLayeredRoots"),
    }


def _validate_requested_ids(
    request: Mapping[str, Any],
    modules: Mapping[str, Any],
    interfaces: Mapping[str, Any],
    stations: Mapping[str, Any],
) -> Dict[str, List[str]]:
    checks = {
        "entryModuleIds": sorted(set(str(v) for v in request.get("entryModuleIds") or []) - set(modules)),
        "terminalModuleIds": sorted(set(str(v) for v in request.get("terminalModuleIds") or []) - set(modules)),
        "requiredModuleIds": sorted(set(str(v) for v in request.get("requiredModuleIds") or []) - set(modules)),
        "supportModuleIds": sorted(set(str(v) for v in request.get("supportModuleIds") or []) - set(modules)),
        "requiredInterfaceIds": sorted(set(str(v) for v in request.get("requiredInterfaceIds") or []) - set(interfaces)),
        "requiredStationIds": sorted(set(str(v) for v in request.get("requiredStationIds") or []) - set(stations)),
    }
    return checks


def _module_identity_maps(
    graph: Mapping[str, Any],
    modules: Mapping[str, Mapping[str, Any]],
    runtime_projection: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    field_ids: MutableMapping[str, Set[str]] = {module_id: set() for module_id in modules}
    schema_ids: MutableMapping[str, Set[str]] = {module_id: set() for module_id in modules}
    interface_ids: MutableMapping[str, Set[str]] = {module_id: set() for module_id in modules}
    station_ids: MutableMapping[str, Set[str]] = {module_id: set() for module_id in modules}

    for field_id, owners in (graph.get("fields") or {}).items():
        for module_id in owners or []:
            field_ids.setdefault(str(module_id), set()).add(str(field_id))
    for schema_id, owners in (graph.get("schemas") or {}).items():
        for module_id in owners or []:
            schema_ids.setdefault(str(module_id), set()).add(str(schema_id))
    for interface_id, owners in (graph.get("interfaces") or {}).items():
        for module_id in owners or []:
            interface_ids.setdefault(str(module_id), set()).add(str(interface_id))
    for station_id, owners in (graph.get("stations") or {}).items():
        for module_id in owners or []:
            station_ids.setdefault(str(module_id), set()).add(str(station_id))

    runtime_modules = dict(runtime_projection.get("modules") or {})
    result: Dict[str, Dict[str, Any]] = {}
    for module_id, module in sorted(modules.items()):
        paths: Set[str] = set()
        runner_path = _runner_path(str(module.get("runner") or ""))
        if runner_path:
            paths.add(runner_path)
        runtime = dict(runtime_modules.get(module_id) or {})
        paths.update(str(path) for path in runtime.get("implementationPaths") or [] if str(path))
        result[module_id] = {
            "moduleId": module_id,
            "status": module.get("status"),
            "activationState": module.get("activationState"),
            "owner": module.get("owner"),
            "runner": module.get("runner"),
            "upstream": sorted(set(str(v) for v in module.get("upstream") or [])),
            "downstream": sorted(set(str(v) for v in module.get("downstream") or [])),
            "fieldIds": sorted(field_ids.get(module_id) or []),
            "schemaIds": sorted(schema_ids.get(module_id) or []),
            "interfaceIds": sorted(interface_ids.get(module_id) or []),
            "stationIds": sorted(station_ids.get(module_id) or []),
            "physicalPaths": sorted(paths),
        }
    return result


def _render_review_markdown(report: Mapping[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    findings = list(report.get("findings") or [])
    lines = [
        "# Contest Chain Review",
        "",
        f"- Review state: `{report.get('reviewState')}`",
        f"- Baseline commit: `{report.get('baselineCommit')}`",
        f"- Graph hash: `{report.get('graphHash')}`",
        f"- Selection hash: `{report.get('selectionHash')}`",
        f"- Registry adapter state: `{report.get('registryAdapterState')}`",
        "",
        "## Summary",
        "",
        f"- KEEP_CORE: {summary.get('KEEP_CORE', 0)}",
        f"- KEEP_SUPPORT: {summary.get('KEEP_SUPPORT', 0)}",
        f"- ISOLATE: {summary.get('ISOLATE', 0)}",
        f"- REMOVE_CANDIDATE: {summary.get('REMOVE_CANDIDATE', 0)}",
        f"- REVIEW_REQUIRED: {summary.get('REVIEW_REQUIRED', 0)}",
        "",
        "## Machine findings",
        "",
    ]
    if findings:
        lines.extend(f"- `{item}`" for item in findings)
    else:
        lines.append("- None")

    for classification in (
        "KEEP_CORE",
        "KEEP_SUPPORT",
        "ISOLATE",
        "REMOVE_CANDIDATE",
        "REVIEW_REQUIRED",
    ):
        lines.extend(
            [
                "",
                f"## {classification}",
                "",
                "| Module | Status | Owner | Upstream | Downstream | Physical paths |",
                "|---|---|---|---|---|---|",
            ]
        )
        for item in report.get("moduleReview") or []:
            if item.get("classification") != classification:
                continue
            lines.append(
                "| {module} | {status} | {owner} | {upstream} | {downstream} | {paths} |".format(
                    module=item.get("moduleId"),
                    status=item.get("status") or "",
                    owner=item.get("owner") or "",
                    upstream="<br>".join(item.get("upstream") or []) or "-",
                    downstream="<br>".join(item.get("downstream") or []) or "-",
                    paths="<br>".join(item.get("physicalPaths") or []) or "-",
                )
            )

    lines.extend(
        [
            "",
            "## Approval gate",
            "",
            "No physical deletion is authorized. Move to `APPROVED` only after human review of",
            "root equivalence, active unresolved modules, tombstone hits, and shared physical paths.",
            "",
        ]
    )
    return "\n".join(lines)


def run(root: Path, request_path: Path, output_dir: Path, z_source: Path) -> Dict[str, Any]:
    root = root.resolve()
    request = _read_object(request_path)
    runtime_projection = _read_object(root / "config" / "v23_registry_runtime.json")
    adapter_layout = _read_object(root / ".z" / "adapter" / "registry-layout.json")
    dependency_manifest = _read_object(root / ".z" / "dependency-manifest.json")

    external_compile_path = z_source / "tools" / "registry_compiler" / "compile_registry.py"
    external_compile = _load_module(external_compile_path, "_contest_z_external_compile_registry")

    documents, adapter_check = _load_adapter_documents(root, external_compile.sha256_value)

    # Bind the mother repository graph/audit implementations to the product adapter.
    import tools.registry_compiler.compile_registry as product_compile

    def adapter_load_registry_documents(_root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
        repository = Path(_root).resolve() if _root is not None else root
        loaded, _check = _load_adapter_documents(repository, external_compile.sha256_value)
        return loaded

    product_compile.load_registry_documents = adapter_load_registry_documents
    product_compile.sha256_value = external_compile.sha256_value

    from tools.registry_compiler.registry_graph import build_dependency_graph
    from tools.registry_compiler.repository_audit import scan_repository

    modules = _records(documents["modules.json"], "modules", "moduleId")
    interfaces = _records(documents["interfaces.json"], "interfaces", "interfaceId")
    stations = _records(documents["stations.json"], "stations", "stationId")

    missing_ids = _validate_requested_ids(request, modules, interfaces, stations)
    missing_total = sum(len(values) for values in missing_ids.values())

    graph = build_dependency_graph(root)
    repository_scan = scan_repository(root)

    entry_modules = set(str(v) for v in request.get("entryModuleIds") or [])
    terminal_modules = set(str(v) for v in request.get("terminalModuleIds") or [])
    required_modules = set(str(v) for v in request.get("requiredModuleIds") or [])
    support_modules = set(str(v) for v in request.get("supportModuleIds") or [])

    interface_modules: Set[str] = set()
    for interface_id in request.get("requiredInterfaceIds") or []:
        interface_modules.update(str(v) for v in (graph.get("interfaces") or {}).get(interface_id) or [])
    station_modules: Set[str] = set()
    for station_id in request.get("requiredStationIds") or []:
        station_modules.update(str(v) for v in (graph.get("stations") or {}).get(station_id) or [])

    forward = _closure(entry_modules, graph.get("downstream") or {})
    reverse = _closure(terminal_modules, graph.get("upstream") or {})
    path_core = forward & reverse
    explicit_core = required_modules - support_modules
    core = path_core | explicit_core | station_modules | (interface_modules - support_modules)
    support = support_modules | (_closure(core, graph.get("upstream") or {}) - core)
    safe_keep = core | support

    identities = _module_identity_maps(graph, modules, runtime_projection)
    module_review: List[Dict[str, Any]] = []
    classifications: MutableMapping[str, List[str]] = {
        "KEEP_CORE": [],
        "KEEP_SUPPORT": [],
        "ISOLATE": [],
        "REMOVE_CANDIDATE": [],
        "REVIEW_REQUIRED": [],
    }

    for module_id, identity in sorted(identities.items()):
        status = str(identity.get("status") or "")
        activation = str(identity.get("activationState") or "")
        if module_id in core:
            classification = "KEEP_CORE"
            reason = "registered_path_or_explicit_required"
        elif module_id in support:
            classification = "KEEP_SUPPORT"
            reason = "registered_upstream_or_governance_support"
        elif status == "REGISTERED_ONLY" or activation == "REGISTERED_ONLY":
            classification = "ISOLATE"
            reason = "registered_only_runtime_binding_disabled"
        elif status == "AUDIT_ONLY":
            classification = "ISOLATE"
            reason = "audit_only_outside_selected_support"
        else:
            classification = "REVIEW_REQUIRED"
            reason = "active_module_outside_selected_closure"
        classifications[classification].append(module_id)
        module_review.append({**identity, "classification": classification, "reason": reason})

    retired_hits: MutableMapping[str, int] = {}
    for hit in repository_scan.get("retiredFieldCandidateHits") or []:
        candidate = dict(hit.get("candidate") or {})
        legacy = str(candidate.get("legacyPath") or hit.get("key") or "")
        if legacy:
            retired_hits[legacy] = retired_hits.get(legacy, 0) + 1

    tombstone_review: List[Dict[str, Any]] = []
    for raw in documents["tombstones.json"].get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        legacy = str(raw.get("legacyPath") or "")
        hits = int(retired_hits.get(legacy, 0))
        tombstone_review.append(
            {
                **raw,
                "repositoryHitCount": hits,
                "classification": "REMOVE_CANDIDATE" if hits == 0 else "REVIEW_REQUIRED",
            }
        )

    path_claims: MutableMapping[str, Set[str]] = {}
    module_classification = {
        item["moduleId"]: item["classification"] for item in module_review
    }
    for item in module_review:
        for path in item.get("physicalPaths") or []:
            path_claims.setdefault(str(path), set()).add(str(item["moduleId"]))

    physical_review: List[Dict[str, Any]] = []
    for path, claimants in sorted(path_claims.items()):
        claimant_states = sorted(set(module_classification.get(item, "REVIEW_REQUIRED") for item in claimants))
        if any(state in {"KEEP_CORE", "KEEP_SUPPORT"} for state in claimant_states):
            classification = "KEEP_SUPPORT" if "KEEP_CORE" not in claimant_states else "KEEP_CORE"
        elif claimant_states == ["ISOLATE"]:
            classification = "ISOLATE"
        else:
            classification = "REVIEW_REQUIRED"
        physical_review.append(
            {
                "path": path,
                "claimingModuleIds": sorted(claimants),
                "claimantClassifications": claimant_states,
                "classification": classification,
            }
        )

    current_manifest = _read_object(root / "contracts" / "registry" / "registry-manifest.json")
    external_compile_error: Optional[str] = None
    external_generated: Optional[Dict[str, Any]] = None
    try:
        external_generated = external_compile.compile_registry(root, write=False)
    except Exception as exc:  # Protocol difference is review evidence, not a mutation failure.
        external_compile_error = f"{type(exc).__name__}:{exc}"

    roots = dict(adapter_layout.get("observedLayeredRoots") or {})
    layered_root_values = sorted(set(str(value) for value in roots.values() if str(value)))
    root_equivalent = len(layered_root_values) <= 1

    findings: List[str] = []
    if not adapter_check.get("allDocumentsMatch"):
        findings.append("ADAPTER_DOCUMENT_HASH_MISMATCH")
    if missing_total:
        findings.append("SELECTION_REQUEST_UNKNOWN_IDS")
    if not root_equivalent:
        findings.append("LAYERED_REGISTRY_ROOTS_NOT_EQUIVALENT")
    if external_compile_error:
        findings.append("Z_INTERFACE_PRODUCT_TOMBSTONE_PROTOCOL_REVIEW_REQUIRED")
    if repository_scan.get("summary", {}).get("runnerDriftCount"):
        findings.append("REGISTERED_RUNNER_DRIFT_REVIEW_REQUIRED")
    if classifications["REVIEW_REQUIRED"]:
        findings.append("ACTIVE_MODULES_OUTSIDE_SELECTED_CLOSURE")
    if any(item["classification"] == "REVIEW_REQUIRED" for item in tombstone_review):
        findings.append("TOMBSTONE_REFERENCES_REMAIN")

    baseline = {
        "schema": "contest.registry_snapshot.v1",
        "state": "BASELINE_LOCKED",
        "repositoryCommit": _git(root, "rev-parse", "HEAD"),
        "repositoryTreeSha": _git(root, "rev-parse", "HEAD^{tree}"),
        "registryTreeSha": _git(root, "rev-parse", "HEAD:contracts/registry"),
        "registryCompilerTreeSha": _git(root, "rev-parse", "HEAD:tools/registry_compiler"),
        "zAdapterTreeSha": _git(root, "rev-parse", "HEAD:tools/z_adapter"),
        "zMetadataTreeSha": _git(root, "rev-parse", "HEAD:.z"),
        "motherSourceCommit": request.get("baseline", {}).get("motherSourceCommit"),
        "zDependencyCommit": dependency_manifest.get("dependency", {}).get("sourceCommit"),
        "adapterCheck": adapter_check,
        "currentManifestRegistryRootHash": current_manifest.get("registryRootHash"),
        "runtimeProjectionRegistryRootHash": runtime_projection.get("registryRootHash"),
        "observedLayeredRoots": roots,
        "rootEquivalent": root_equivalent,
        "externalZCompile": {
            "verified": external_generated is not None,
            "generatedRegistryRootHash": (external_generated or {}).get("registryRootHash"),
            "error": external_compile_error,
        },
        "missingRequestedIds": missing_ids,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }

    forward_report = {
        "schema": "contest.forward_closure.v1",
        "entryModuleIds": sorted(entry_modules),
        "moduleIds": sorted(forward),
        "graphHash": graph.get("graphHash"),
    }
    forward_report["forwardClosureHash"] = external_compile.sha256_value(forward_report)

    reverse_report = {
        "schema": "contest.reverse_closure.v1",
        "terminalModuleIds": sorted(terminal_modules),
        "moduleIds": sorted(reverse),
        "graphHash": graph.get("graphHash"),
    }
    reverse_report["reverseClosureHash"] = external_compile.sha256_value(reverse_report)

    selection_material = {
        "schema": "contest.selection_manifest.v1",
        "mode": "report_only",
        "request": request,
        "graphHash": graph.get("graphHash"),
        "forwardClosureHash": forward_report["forwardClosureHash"],
        "reverseClosureHash": reverse_report["reverseClosureHash"],
        "pathCoreModuleIds": sorted(path_core),
        "coreModuleIds": sorted(core),
        "supportModuleIds": sorted(support),
        "safeKeepModuleIds": sorted(safe_keep),
        "classifications": {key: sorted(value) for key, value in classifications.items()},
        "tombstoneReview": tombstone_review,
        "physicalPathReview": physical_review,
    }
    selection_hash = external_compile.sha256_value(selection_material)
    selection_manifest = {**selection_material, "selectionHash": selection_hash}

    review = {
        "schema": "contest.chain_review.v1",
        "reviewState": "REVIEW_PENDING",
        "baselineCommit": baseline["repositoryCommit"],
        "registryAdapterState": (
            "REGISTRY_DOCUMENTS_VERIFIED_ROOT_EQUIVALENCE_PENDING"
            if adapter_check.get("allDocumentsMatch") and not root_equivalent
            else "REGISTRY_REVIEW_REQUIRED"
        ),
        "graphHash": graph.get("graphHash"),
        "repositoryScanHash": repository_scan.get("repositoryScanHash"),
        "selectionHash": selection_hash,
        "summary": {key: len(value) for key, value in classifications.items()},
        "findings": sorted(set(findings)),
        "moduleReview": module_review,
        "tombstoneReview": tombstone_review,
        "physicalPathReview": physical_review,
        "approval": request.get("approval"),
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    review["reviewHash"] = external_compile.sha256_value(review)

    receipt = {
        "schema": "contest.selection_execution_receipt.v1",
        "states": [
            "BASELINE_LOCKED",
            "REGISTRY_DOCUMENTS_VERIFIED",
            "LINEAGE_GRAPH_BUILT",
            "CONTEST_CLOSURE_SELECTED",
            "REVIEW_PENDING",
        ],
        "registryRootEquivalencePending": not root_equivalent,
        "graphHash": graph.get("graphHash"),
        "repositoryScanHash": repository_scan.get("repositoryScanHash"),
        "selectionHash": selection_hash,
        "reviewHash": review["reviewHash"],
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    receipt["receiptHash"] = external_compile.sha256_value(receipt)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "registry-snapshot.json", baseline)
    _write_json(output_dir / "hash-lineage-graph.json", graph)
    _write_json(output_dir / "repository-audit.json", repository_scan)
    _write_json(output_dir / "forward-closure.json", forward_report)
    _write_json(output_dir / "reverse-closure.json", reverse_report)
    _write_json(output_dir / "contest-selection-manifest.json", selection_manifest)
    _write_json(output_dir / "contest-chain-review.json", review)
    _write_json(output_dir / "execution-receipt.json", receipt)
    (output_dir / "contest-chain-review.md").write_text(
        _render_review_markdown(review), encoding="utf-8"
    )

    return {
        "reviewState": review["reviewState"],
        "registryAdapterState": review["registryAdapterState"],
        "graphHash": graph.get("graphHash"),
        "selectionHash": selection_hash,
        "reviewHash": review["reviewHash"],
        "receiptHash": receipt["receiptHash"],
        "summary": review["summary"],
        "findings": review["findings"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run report-only contest registry lineage selection.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--request", default="governance/contest/selection-request.json")
    parser.add_argument("--output", default=str(GENERATED_DIR))
    parser.add_argument("--z-source", default=os.environ.get("Z_CENTURY_SOURCE_DIR"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.z_source:
        raise SystemExit("Z_CENTURY_SOURCE_DIR_REQUIRED")
    root = Path(args.root).resolve()
    result = run(
        root,
        (root / args.request).resolve(),
        (root / args.output).resolve(),
        Path(args.z_source).resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
