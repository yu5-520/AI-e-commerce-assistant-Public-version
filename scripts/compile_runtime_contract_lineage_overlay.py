#!/usr/bin/env python3
"""Compile canonical runtime field/interface lineage on top of competition lineage.

The base competition compiler proves the actual production import/reference closure.
This overlay then projects the unified contract registry onto that proven file graph,
so every repair-relevant field/interface has an owner, producers, consumers, aliases
and deterministic lineage hash. Stdlib only; no business runtime import and no venv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

SCHEMA = "runtime.contract_lineage.overlay.v1"


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

    nodes: dict[str, Dict[str, Any]] = {}
    edges: set[tuple[str, str, str]] = set()

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

    for section, node_type in (("fields", "canonical_field"), ("interfaces", "canonical_interface")):
        records = registry.get(section) or {}
        if not isinstance(records, dict):
            findings.append(f"registry_section_invalid:{section}")
            continue
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
                "invariants": strings(raw.get("invariants") or []),
                "repairPolicy": raw.get("repairPolicy"),
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
        if source_prefix:
            for canonical_id, field in (registry.get("fields") or {}).items():
                if canonical_id != "agent3.system_contract_violations" or not isinstance(field, dict):
                    continue
                edges.add((f"contract:field:{canonical_id}", node_id, "CLASSIFIES_TO"))

    # Every central-registry producer/consumer must be visible in the actual runtime
    # closure; this is the crucial registry -> hash-lineage consistency gate.
    base_graph_hash = str(base_graph.get("graphHash") or "")
    if not base_graph_hash.startswith("sha256:"):
        findings.append("base_lineage_graph_hash_missing")

    overlay_nodes = sorted(nodes.values(), key=lambda item: item["id"])
    overlay_edges = [
        {"from": source, "to": target, "type": edge_type}
        for source, target, edge_type in sorted(edges)
    ]
    graph_material = {
        "schema": SCHEMA,
        "registryVersion": registry.get("version"),
        "baseLineageGraphHash": base_graph_hash,
        "nodes": overlay_nodes,
        "edges": overlay_edges,
    }
    overlay_hash = canonical_hash(graph_material)
    report_material = {
        "schema": "runtime.contract_lineage.overlay_verification.v1",
        "registryVersion": registry.get("version"),
        "baseLineageGraphHash": base_graph_hash,
        "overlayHash": overlay_hash,
        "canonicalFieldCount": len(registry.get("fields") or {}),
        "canonicalInterfaceCount": len(registry.get("interfaces") or {}),
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
        default="dist/competition-contract-lineage/lineage-graph.json",
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
