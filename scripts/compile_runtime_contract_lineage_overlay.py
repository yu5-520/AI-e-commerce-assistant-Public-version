#!/usr/bin/env python3
"""Compile canonical runtime field/interface lineage on top of competition lineage.

The base competition compiler proves the actual production import/reference closure.
This overlay projects the unified contract registry onto that proven file graph and
adds explicit registered field/interface edges and impact edges.  Only registry edges
marked as hard/reference semantics are eligible to become hard broken-lineage errors;
content fingerprints and business identity keys remain visible in the graph without
being misclassified as foreign keys.

Stdlib only; no business runtime import and no venv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

SCHEMA = "runtime.contract_lineage.overlay.v2"


class OverlayError(RuntimeError):
    pass


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


def read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OverlayError(f"json_object_required:{path}")
    return value


def write_object(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def owner_file(owner: str) -> str:
    module = str(owner or "").partition(":")[0].strip()
    if not module:
        return ""
    return Path(*module.split(".")).with_suffix(".py").as_posix()


def strings(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def compile_overlay(
    root: Path,
    *,
    registry: Mapping[str, Any],
    base_graph: Mapping[str, Any],
) -> Dict[str, Any]:
    findings: list[str] = []
    warnings: list[str] = []
    base_nodes = {
        str(item.get("id")): dict(item)
        for item in base_graph.get("nodes") or []
        if isinstance(item, dict) and item.get("id")
    }
    runtime_files = {
        node_id.removeprefix("file:")
        for node_id, item in base_nodes.items()
        if item.get("type") == "file" and node_id.startswith("file:")
    }

    fields = registry.get("fields") or {}
    interfaces = registry.get("interfaces") or {}
    if not isinstance(fields, dict):
        findings.append("registry_section_invalid:fields")
        fields = {}
    if not isinstance(interfaces, dict):
        findings.append("registry_section_invalid:interfaces")
        interfaces = {}

    nodes: dict[str, Dict[str, Any]] = {}
    edges: set[tuple[str, str, str]] = set()
    explicit_edge_metadata: dict[tuple[str, str, str], Dict[str, Any]] = {}

    def add_file_edge(source: str, relative: str, edge_type: str) -> None:
        if not relative:
            findings.append(f"empty_file_binding:{source}:{edge_type}")
            return
        path = root / relative
        if not path.is_file():
            findings.append(f"registered_file_missing:{source}:{relative}")
            return
        if relative not in runtime_files:
            findings.append(f"registered_file_outside_runtime_lineage:{source}:{relative}")
        edges.add((source, f"file:{relative}", edge_type))

    def contract_node(canonical_id: str) -> str:
        value = str(canonical_id or "").strip()
        if value in fields:
            return f"contract:field:{value}"
        if value in interfaces:
            return f"contract:interface:{value}"
        return ""

    for section, records, node_type in (
        ("fields", fields, "canonical_field"),
        ("interfaces", interfaces, "canonical_interface"),
    ):
        for canonical_id, raw in sorted(records.items()):
            if not isinstance(raw, dict):
                findings.append(f"registry_record_invalid:{section}:{canonical_id}")
                continue
            node_id = f"contract:{section[:-1]}:{canonical_id}"
            owner = str(raw.get("owner") or "").strip()
            owner_path = owner_file(owner)
            aliases = strings(
                raw.get("legacyAliases")
                or raw.get("compatibilityAliases")
                or []
            )
            nodes[node_id] = {
                "id": node_id,
                "type": node_type,
                "canonicalId": canonical_id,
                "owner": owner,
                "ownerPath": owner_path,
                "aliases": aliases,
                "semantic": raw.get("semantic"),
                "invariants": strings(raw.get("invariants") or []),
                "repairPolicy": raw.get("repairPolicy"),
                "inputFields": strings(raw.get("inputFields") or []),
                "outputFields": strings(raw.get("outputFields") or []),
            }
            if not owner:
                findings.append(f"canonical_owner_missing:{canonical_id}")
            else:
                add_file_edge(node_id, owner_path, "OWNED_BY")

            for relative in strings(raw.get("producers") or []):
                add_file_edge(node_id, relative, "PRODUCED_BY")
            for relative in strings(raw.get("consumers") or []):
                add_file_edge(node_id, relative, "CONSUMED_BY")
            for alias in aliases:
                alias_id = f"alias:{canonical_id}:{alias}"
                nodes[alias_id] = {
                    "id": alias_id,
                    "type": "legacy_alias",
                    "canonicalId": canonical_id,
                    "alias": alias,
                    "readCompatibilityOnly": True,
                }
                edges.add((alias_id, node_id, "ALIASES"))

            for fallback in strings(raw.get("forbiddenFallbacks") or []):
                fallback_id = f"forbidden-fallback:{canonical_id}:{fallback}"
                nodes[fallback_id] = {
                    "id": fallback_id,
                    "type": "forbidden_fallback",
                    "canonicalId": canonical_id,
                    "fallback": fallback,
                }
                edges.add((node_id, fallback_id, "FORBIDS"))

    for interface_id, raw in sorted(interfaces.items()):
        if not isinstance(raw, dict):
            continue
        interface_node = f"contract:interface:{interface_id}"
        for field_id in strings(raw.get("inputFields") or []):
            field_node = contract_node(field_id)
            if not field_node:
                findings.append(f"interface_input_field_unregistered:{interface_id}:{field_id}")
                continue
            edges.add((field_node, interface_node, "INPUT_TO"))
        for field_id in strings(raw.get("outputFields") or []):
            field_node = contract_node(field_id)
            if not field_node:
                findings.append(f"interface_output_field_unregistered:{interface_id}:{field_id}")
                continue
            edges.add((interface_node, field_node, "OUTPUTS"))

    explicit_edges = registry.get("lineageEdges") or []
    if not isinstance(explicit_edges, list):
        findings.append("lineage_edges_invalid")
        explicit_edges = []
    hard_edge_count = 0
    for index, raw in enumerate(explicit_edges):
        if not isinstance(raw, dict):
            findings.append(f"lineage_edge_invalid:{index}")
            continue
        source_id = str(raw.get("from") or "").strip()
        target_id = str(raw.get("to") or "").strip()
        edge_type = str(raw.get("type") or "CONTRACT_LINEAGE").strip().upper()
        source = contract_node(source_id)
        target = contract_node(target_id)
        if not source:
            findings.append(f"lineage_edge_source_unregistered:{source_id}")
            continue
        if not target:
            findings.append(f"lineage_edge_target_unregistered:{target_id}")
            continue
        key = (source, target, edge_type)
        edges.add(key)
        metadata = {
            "required": bool(raw.get("required")),
            "sourceCanonicalId": source_id,
            "targetCanonicalId": target_id,
        }
        explicit_edge_metadata[key] = metadata
        if edge_type in {"HARD_POINTER", "EXACT_REFERENCE_TRANSFER", "EXACT_HASH_DERIVATION"}:
            hard_edge_count += 1

    impacts = registry.get("impacts") or []
    if not isinstance(impacts, list):
        findings.append("impacts_invalid")
        impacts = []
    impact_edge_count = 0
    for index, raw in enumerate(impacts):
        if not isinstance(raw, dict):
            findings.append(f"impact_invalid:{index}")
            continue
        source_id = str(raw.get("source") or "").strip()
        source = contract_node(source_id)
        if not source:
            findings.append(f"impact_source_unregistered:{source_id}")
            continue
        for target_id in strings(raw.get("targets") or []):
            target = contract_node(target_id)
            if not target:
                findings.append(f"impact_target_unregistered:{source_id}:{target_id}")
                continue
            edges.add((source, target, "IMPACTS"))
            impact_edge_count += 1

    repair_classes = registry.get("repairClasses") or {}
    if not isinstance(repair_classes, dict):
        findings.append("repair_classes_invalid")
        repair_classes = {}
    for name, raw in sorted(repair_classes.items()):
        if not isinstance(raw, dict):
            findings.append(f"repair_class_invalid:{name}")
            continue
        node_id = f"repair-class:{name}"
        nodes[node_id] = {
            "id": node_id,
            "type": "repair_class",
            "repairable": bool(raw.get("repairable")),
            "repairMode": raw.get("repairMode"),
            "sourcePrefix": raw.get("sourcePrefix"),
        }
        source_prefix = str(raw.get("sourcePrefix") or "")
        if source_prefix and "agent3.system_contract_violations" in fields:
            edges.add((
                "contract:field:agent3.system_contract_violations",
                node_id,
                "CLASSIFIES_TO",
            ))

    base_graph_hash = str(base_graph.get("graphHash") or "")
    if not base_graph_hash.startswith("sha256:"):
        findings.append("base_lineage_graph_hash_missing")

    overlay_nodes = sorted(nodes.values(), key=lambda item: item["id"])
    overlay_edges = []
    for source, target, edge_type in sorted(edges):
        item: Dict[str, Any] = {"from": source, "to": target, "type": edge_type}
        if (source, target, edge_type) in explicit_edge_metadata:
            item.update(explicit_edge_metadata[(source, target, edge_type)])
        overlay_edges.append(item)

    graph_material = {
        "schema": SCHEMA,
        "registryVersion": registry.get("version"),
        "baseLineageGraphHash": base_graph_hash,
        "nodes": overlay_nodes,
        "edges": overlay_edges,
    }
    overlay_hash = canonical_hash(graph_material)
    report_material = {
        "schema": "runtime.contract_lineage.overlay_verification.v2",
        "registryVersion": registry.get("version"),
        "baseLineageGraphHash": base_graph_hash,
        "overlayHash": overlay_hash,
        "canonicalFieldCount": len(fields),
        "canonicalInterfaceCount": len(interfaces),
        "registeredLineageEdgeCount": len(explicit_edges),
        "hardLineageEdgeCount": hard_edge_count,
        "impactEdgeCount": impact_edge_count,
        "repairClassCount": len(repair_classes),
        "runtimeFileCount": len(runtime_files),
        "overlayNodeCount": len(overlay_nodes),
        "overlayEdgeCount": len(overlay_edges),
        "findings": sorted(set(findings)),
        "warnings": sorted(set(warnings)),
    }
    report = {
        **report_material,
        "verified": not findings,
        "verificationHash": canonical_hash(report_material),
    }
    return {
        **graph_material,
        "overlayHash": overlay_hash,
        "verification": report,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compile runtime contract lineage overlay")
    parser.add_argument("--root", default=None)
    parser.add_argument(
        "--registry",
        default="config/runtime_contract_lineage_registry_v1.json",
    )
    parser.add_argument(
        "--base-lineage",
        default="dist/competition-lineage/lineage-graph.json",
    )
    parser.add_argument(
        "--output",
        default="dist/competition-contract-lineage/runtime-contract-lineage.json",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    registry = read_object(root / args.registry)
    base_graph = read_object(root / args.base_lineage)
    compiled = compile_overlay(root, registry=registry, base_graph=base_graph)
    write_object(root / args.output, compiled)
    report = compiled["verification"]
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "overlayHash": compiled["overlayHash"],
                "baseLineageGraphHash": report["baseLineageGraphHash"],
                "canonicalFieldCount": report["canonicalFieldCount"],
                "canonicalInterfaceCount": report["canonicalInterfaceCount"],
                "registeredLineageEdgeCount": report["registeredLineageEdgeCount"],
                "hardLineageEdgeCount": report["hardLineageEdgeCount"],
                "impactEdgeCount": report["impactEdgeCount"],
                "overlayEdgeCount": report["overlayEdgeCount"],
                "findings": report["findings"],
                "output": args.output,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
