#!/usr/bin/env python3
"""Compare BASE and TARGET competition hash-lineage evidence and gate update scope.

This script turns the existing lineage compiler from a release-only verifier into an
update controller. It never guesses files by name. Instead it compares the compiled
runtime closure, file hashes, registry ownership and graph edges from two exact source
commits, then applies a fail-closed policy profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "competition.lineage_update_verification.v1"
SPECIAL_HASH_FIELDS = (
    "registryRootHash",
    "productBoundaryHash",
    "externalInterfaceRegistryHash",
    "interfaceGovernanceHash",
)


class CompetitionLineageUpdateError(RuntimeError):
    """Raised when evidence or policy cannot be interpreted safely."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CompetitionLineageUpdateError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise CompetitionLineageUpdateError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def evidence(directory: Path) -> dict[str, Any]:
    required = {
        "graph": "lineage-graph.json",
        "verification": "verification-report.json",
        "registry": "registry-snapshot.json",
    }
    loaded: dict[str, Any] = {}
    for key, name in required.items():
        path = directory / name
        if not path.is_file():
            raise CompetitionLineageUpdateError(f"EVIDENCE_FILE_MISSING:{path}")
        loaded[key] = read_object(path)
    return loaded


def node_map(graph: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("id") or "").strip()
        if not node_id:
            continue
        result[node_id] = dict(raw)
    return result


def edge_set(graph: Mapping[str, Any]) -> set[tuple[str, str, str]]:
    result: set[tuple[str, str, str]] = set()
    for raw in graph.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("from") or "").strip()
        target = str(raw.get("to") or "").strip()
        edge_type = str(raw.get("type") or "").strip()
        if source and target and edge_type:
            result.add((source, target, edge_type))
    return result


def file_nodes(nodes: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in nodes.values():
        if raw.get("type") != "file":
            continue
        path = str(raw.get("path") or "").strip()
        if path:
            result[path] = dict(raw)
    return result


def normalized_node(node: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in node.items() if key not in {"sourceCommit"}}


def module_map(registry: Mapping[str, Any]) -> dict[str, Any]:
    raw = registry.get("modules") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def prefix_allowed(value: str, prefixes: Sequence[str]) -> bool:
    for prefix in prefixes:
        clean = str(prefix)
        if clean.endswith("/"):
            if value.startswith(clean):
                return True
        elif value == clean or value.startswith(clean):
            return True
    return False


def file_change_allowed(
    path: str,
    node: Mapping[str, Any],
    *,
    allowed_path_prefixes: Sequence[str],
    allowed_registry_modules: set[str],
) -> bool:
    if prefix_allowed(path, allowed_path_prefixes):
        return True
    owners = {str(item) for item in node.get("registryModules") or []}
    return bool(owners & allowed_registry_modules)


def node_change_allowed(
    node_id: str,
    node: Mapping[str, Any],
    *,
    allowed_path_prefixes: Sequence[str],
    allowed_registry_modules: set[str],
    allowed_node_prefixes: Sequence[str],
) -> bool:
    if prefix_allowed(node_id, allowed_node_prefixes):
        return True
    if node_id.startswith("file:"):
        path = str(node.get("path") or node_id.removeprefix("file:"))
        return file_change_allowed(
            path,
            node,
            allowed_path_prefixes=allowed_path_prefixes,
            allowed_registry_modules=allowed_registry_modules,
        )
    if node_id.startswith("registry:"):
        module_id = str(node.get("moduleId") or node_id.removeprefix("registry:"))
        return module_id in allowed_registry_modules
    return False


def compare(
    baseline: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    profile_name: str,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    findings: list[str] = []
    warnings: list[str] = []

    base_graph = baseline["graph"]
    target_graph = target["graph"]
    base_ver = baseline["verification"]
    target_ver = target["verification"]
    base_registry = baseline["registry"]
    target_registry = target["registry"]

    if bool(profile.get("requireBaselineVerified", True)) and base_ver.get("verified") is not True:
        findings.append("BASELINE_LINEAGE_NOT_VERIFIED")
    if bool(profile.get("requireTargetVerified", True)) and target_ver.get("verified") is not True:
        findings.append("TARGET_LINEAGE_NOT_VERIFIED")

    base_nodes = node_map(base_graph)
    target_nodes = node_map(target_graph)
    base_files = file_nodes(base_nodes)
    target_files = file_nodes(target_nodes)

    base_file_paths = set(base_files)
    target_file_paths = set(target_files)
    added_runtime_files = sorted(target_file_paths - base_file_paths)
    removed_runtime_files = sorted(base_file_paths - target_file_paths)
    changed_runtime_files = sorted(
        path
        for path in base_file_paths & target_file_paths
        if normalized_node(base_files[path]) != normalized_node(target_files[path])
    )

    base_node_ids = set(base_nodes)
    target_node_ids = set(target_nodes)
    added_nodes = sorted(target_node_ids - base_node_ids)
    removed_nodes = sorted(base_node_ids - target_node_ids)
    changed_nodes = sorted(
        node_id
        for node_id in base_node_ids & target_node_ids
        if normalized_node(base_nodes[node_id]) != normalized_node(target_nodes[node_id])
    )

    base_edges = edge_set(base_graph)
    target_edges = edge_set(target_graph)
    added_edges = sorted(target_edges - base_edges)
    removed_edges = sorted(base_edges - target_edges)

    base_modules = module_map(base_registry)
    target_modules = module_map(target_registry)
    module_ids = set(base_modules) | set(target_modules)
    changed_registry_modules = sorted(
        module_id
        for module_id in module_ids
        if base_modules.get(module_id) != target_modules.get(module_id)
    )

    runtime_hash_changed = base_ver.get("runtimeHash") != target_ver.get("runtimeHash")
    graph_hash_changed = base_ver.get("graphHash") != target_ver.get("graphHash")
    special_hash_changes = {
        field: {
            "baseline": base_ver.get(field),
            "target": target_ver.get(field),
        }
        for field in SPECIAL_HASH_FIELDS
        if base_ver.get(field) != target_ver.get(field)
    }

    if bool(profile.get("requireRuntimeHashStable", False)) and runtime_hash_changed:
        findings.append("RUNTIME_HASH_CHANGED_BUT_PROFILE_REQUIRES_STABILITY")
    if bool(profile.get("requireGraphHashStable", False)) and graph_hash_changed:
        findings.append("GRAPH_HASH_CHANGED_BUT_PROFILE_REQUIRES_STABILITY")

    if not bool(profile.get("allowRuntimeMembershipChange", False)):
        for path in added_runtime_files:
            findings.append(f"RUNTIME_FILE_ADDED_OUTSIDE_PROFILE:{path}")
        for path in removed_runtime_files:
            findings.append(f"RUNTIME_FILE_REMOVED_OUTSIDE_PROFILE:{path}")

    allowed_path_prefixes = text_list(profile.get("allowedRuntimePathPrefixes"))
    allowed_registry_modules = set(text_list(profile.get("allowedRegistryModules")))
    allowed_node_prefixes = text_list(profile.get("allowedNodePrefixes"))

    for path in changed_runtime_files + added_runtime_files + removed_runtime_files:
        node = target_files.get(path) or base_files.get(path) or {}
        if not file_change_allowed(
            path,
            node,
            allowed_path_prefixes=allowed_path_prefixes,
            allowed_registry_modules=allowed_registry_modules,
        ):
            findings.append(f"UNALLOWED_RUNTIME_FILE_CHANGE:{path}")

    if changed_registry_modules:
        if not bool(profile.get("allowRegistryModuleChanges", False)):
            for module_id in changed_registry_modules:
                findings.append(f"REGISTRY_MODULE_CHANGED_OUTSIDE_PROFILE:{module_id}")
        else:
            for module_id in changed_registry_modules:
                if module_id not in allowed_registry_modules:
                    findings.append(f"UNALLOWED_REGISTRY_MODULE_CHANGE:{module_id}")

    changed_edge_records = [
        {"from": source, "to": target_id, "type": edge_type, "change": "added"}
        for source, target_id, edge_type in added_edges
    ] + [
        {"from": source, "to": target_id, "type": edge_type, "change": "removed"}
        for source, target_id, edge_type in removed_edges
    ]
    if changed_edge_records:
        if not bool(profile.get("allowEdgeChanges", False)):
            for edge in changed_edge_records:
                findings.append(
                    "GRAPH_EDGE_CHANGED_OUTSIDE_PROFILE:"
                    f"{edge['change']}:{edge['from']}->{edge['to']}:{edge['type']}"
                )
        else:
            for edge in changed_edge_records:
                endpoints = (edge["from"], edge["to"])
                if not any(
                    node_change_allowed(
                        node_id,
                        target_nodes.get(node_id) or base_nodes.get(node_id) or {},
                        allowed_path_prefixes=allowed_path_prefixes,
                        allowed_registry_modules=allowed_registry_modules,
                        allowed_node_prefixes=allowed_node_prefixes,
                    )
                    for node_id in endpoints
                ):
                    findings.append(
                        "UNALLOWED_GRAPH_EDGE_CHANGE:"
                        f"{edge['change']}:{edge['from']}->{edge['to']}:{edge['type']}"
                    )

    allowed_special_hash_changes = set(text_list(profile.get("allowedSpecialHashChanges")))
    for field in sorted(special_hash_changes):
        if field not in allowed_special_hash_changes:
            findings.append(f"UNALLOWED_SPECIAL_HASH_CHANGE:{field}")

    # Node changes that are not explained by runtime files or allowed registry modules
    # are still checked, so new interface/governance graph nodes cannot appear silently.
    runtime_node_ids = {f"file:{path}" for path in base_file_paths | target_file_paths}
    for node_id in added_nodes + removed_nodes + changed_nodes:
        if node_id in runtime_node_ids:
            continue
        node = target_nodes.get(node_id) or base_nodes.get(node_id) or {}
        if node_id.startswith("registry:") and str(
            node.get("moduleId") or node_id.removeprefix("registry:")
        ) in allowed_registry_modules:
            continue
        if not node_change_allowed(
            node_id,
            node,
            allowed_path_prefixes=allowed_path_prefixes,
            allowed_registry_modules=allowed_registry_modules,
            allowed_node_prefixes=allowed_node_prefixes,
        ):
            findings.append(f"UNALLOWED_NON_FILE_NODE_CHANGE:{node_id}")

    diff_material = {
        "schema": SCHEMA_VERSION,
        "profile": profile_name,
        "baselineCommit": base_ver.get("sourceCommit"),
        "targetCommit": target_ver.get("sourceCommit"),
        "baselineRuntimeHash": base_ver.get("runtimeHash"),
        "targetRuntimeHash": target_ver.get("runtimeHash"),
        "baselineGraphHash": base_ver.get("graphHash"),
        "targetGraphHash": target_ver.get("graphHash"),
        "runtimeHashChanged": runtime_hash_changed,
        "graphHashChanged": graph_hash_changed,
        "addedRuntimeFiles": added_runtime_files,
        "removedRuntimeFiles": removed_runtime_files,
        "changedRuntimeFiles": changed_runtime_files,
        "addedNodes": added_nodes,
        "removedNodes": removed_nodes,
        "changedNodes": changed_nodes,
        "addedEdges": [
            {"from": source, "to": target_id, "type": edge_type}
            for source, target_id, edge_type in added_edges
        ],
        "removedEdges": [
            {"from": source, "to": target_id, "type": edge_type}
            for source, target_id, edge_type in removed_edges
        ],
        "changedRegistryModules": changed_registry_modules,
        "specialHashChanges": special_hash_changes,
        "findings": sorted(set(findings)),
        "warnings": sorted(set(warnings)),
    }
    return {
        **diff_material,
        "verified": not findings,
        "updateHash": canonical_hash(diff_material),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify a BASE -> TARGET competition hash-lineage update."
    )
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--target-dir", required=True)
    parser.add_argument(
        "--policy",
        default="governance/competition_lineage_update_policy.json",
    )
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--output",
        default="dist/competition-lineage-update-report.json",
    )
    parser.add_argument("--allow-findings", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    policy = read_object(Path(args.policy))
    profiles = policy.get("profiles")
    if not isinstance(profiles, dict):
        raise CompetitionLineageUpdateError("POLICY_PROFILES_OBJECT_REQUIRED")
    profile_name = str(args.profile or policy.get("defaultProfile") or "").strip()
    if not profile_name or profile_name not in profiles:
        raise CompetitionLineageUpdateError(f"POLICY_PROFILE_NOT_FOUND:{profile_name}")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        raise CompetitionLineageUpdateError(f"POLICY_PROFILE_OBJECT_REQUIRED:{profile_name}")

    report = compare(
        evidence(Path(args.baseline_dir)),
        evidence(Path(args.target_dir)),
        profile_name=profile_name,
        profile=profile,
    )
    write_json(Path(args.output), report)
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "profile": report["profile"],
                "baselineCommit": report["baselineCommit"],
                "targetCommit": report["targetCommit"],
                "runtimeHashChanged": report["runtimeHashChanged"],
                "graphHashChanged": report["graphHashChanged"],
                "changedRuntimeFiles": len(report["changedRuntimeFiles"]),
                "addedRuntimeFiles": len(report["addedRuntimeFiles"]),
                "removedRuntimeFiles": len(report["removedRuntimeFiles"]),
                "changedRegistryModules": report["changedRegistryModules"],
                "findings": report["findings"],
                "updateHash": report["updateHash"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if report["verified"] or args.allow_findings:
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition lineage update verification failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
