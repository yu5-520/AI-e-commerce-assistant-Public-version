#!/usr/bin/env python3
"""Map misplaced competition repair intents onto the competition repository lineage.

The source PR is treated only as a problem/repair-intent reference. This planner never
copies source paths and never searches by filename similarity. Candidate scope comes
from the competition registry snapshot and compiled hash-lineage graph.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "competition.recovery_scope_plan.v1"


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
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _module_map(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = registry.get("modules") or {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(module_id): dict(value)
        for module_id, value in raw.items()
        if isinstance(value, dict)
    }


def _node_map(graph: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in graph.get("nodes") or []:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("id") or "").strip()
        if node_id:
            result[node_id] = dict(raw)
    return result


def _edges(graph: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in graph.get("edges") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("from") or "").strip()
        target = str(raw.get("to") or "").strip()
        edge_type = str(raw.get("type") or "").strip()
        if source and target and edge_type:
            result.append({"from": source, "to": target, "type": edge_type})
    return result


def _owned_file_nodes(
    nodes: Mapping[str, Mapping[str, Any]],
    anchor_modules: set[str],
) -> set[str]:
    result: set[str] = set()
    for node_id, node in nodes.items():
        if node.get("type") != "file":
            continue
        owners = {str(item) for item in node.get("registryModules") or []}
        if owners & anchor_modules:
            result.add(node_id)
    return result


def _expand_one_hop(
    seed_nodes: set[str],
    edges: Sequence[Mapping[str, str]],
) -> set[str]:
    allowed_types = {"REGISTRY_OWNS", "RUNS", "IMPORTS"}
    result = set(seed_nodes)
    for edge in edges:
        if edge.get("type") not in allowed_types:
            continue
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        if source in seed_nodes or target in seed_nodes:
            result.add(source)
            result.add(target)
    return result


def _candidate_paths(
    nodes: Mapping[str, Mapping[str, Any]],
    node_ids: set[str],
) -> list[str]:
    paths = {
        str(nodes[node_id].get("path") or "")
        for node_id in node_ids
        if node_id in nodes and nodes[node_id].get("type") == "file"
    }
    return sorted(path for path in paths if path)


def _available_fields(
    modules: Mapping[str, Mapping[str, Any]],
    anchor_modules: Sequence[str],
) -> set[str]:
    result: set[str] = set()
    for module_id in anchor_modules:
        module = modules.get(module_id) or {}
        result.update(str(item) for item in module.get("fieldIds") or [])
    return result


def build_plan(
    *,
    recovery: Mapping[str, Any],
    registry: Mapping[str, Any],
    graph: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    if verification.get("verified") is not True:
        raise RuntimeError("TARGET_LINEAGE_NOT_VERIFIED")

    modules = _module_map(registry)
    nodes = _node_map(graph)
    edges = _edges(graph)
    findings: list[str] = []
    requirement_plans: list[dict[str, Any]] = []

    requirements = recovery.get("requirements") or []
    if not isinstance(requirements, list):
        raise RuntimeError("RECOVERY_REQUIREMENTS_LIST_REQUIRED")

    for raw in requirements:
        if not isinstance(raw, dict):
            continue
        requirement_id = str(raw.get("id") or "").strip()
        title = str(raw.get("title") or "").strip()
        disposition = str(raw.get("disposition") or "").strip()
        anchors = [str(item) for item in raw.get("anchorRegistryModules") or []]
        required_fields = [str(item) for item in raw.get("requiredFieldIds") or []]

        missing_modules = sorted(module_id for module_id in anchors if module_id not in modules)
        if missing_modules:
            findings.append(
                f"{requirement_id}:ANCHOR_REGISTRY_MODULE_MISSING:{','.join(missing_modules)}"
            )

        available_fields = _available_fields(modules, anchors)
        missing_fields = sorted(field for field in required_fields if field not in available_fields)

        anchor_set = set(anchors)
        seed_nodes = {f"registry:{module_id}" for module_id in anchors if module_id in modules}
        seed_nodes.update(_owned_file_nodes(nodes, anchor_set))
        scoped_nodes = _expand_one_hop(seed_nodes, edges)
        scoped_nodes = {node_id for node_id in scoped_nodes if node_id in nodes}
        candidate_paths = _candidate_paths(nodes, scoped_nodes)

        structural_status = (
            "do_not_migrate"
            if disposition == "do_not_migrate"
            else "missing_required_fields"
            if missing_fields
            else "structurally_present"
        )
        if disposition == "verify_existing" and missing_fields:
            findings.append(
                f"{requirement_id}:EXPECTED_EXISTING_FIELDS_MISSING:{','.join(missing_fields)}"
            )

        requirement_plans.append(
            {
                "id": requirement_id,
                "title": title,
                "disposition": disposition,
                "structuralStatus": structural_status,
                "behaviorVerificationRequired": bool(raw.get("behaviorVerificationRequired", True)),
                "anchorRegistryModules": anchors,
                "requiredFieldIds": required_fields,
                "availableRequiredFieldIds": sorted(set(required_fields) - set(missing_fields)),
                "missingRequiredFieldIds": missing_fields,
                "candidateNodeIds": sorted(scoped_nodes),
                "candidateRuntimePaths": candidate_paths,
                "runtimeChecks": list(raw.get("runtimeChecks") or []),
                "reason": raw.get("reason"),
            }
        )

    material = {
        "schema": SCHEMA_VERSION,
        "sourceCommit": verification.get("sourceCommit"),
        "runtimeHash": verification.get("runtimeHash"),
        "graphHash": verification.get("graphHash"),
        "sourceReference": recovery.get("sourceReference"),
        "rules": recovery.get("rules"),
        "requirements": requirement_plans,
        "findings": sorted(set(findings)),
    }
    return {
        **material,
        "verified": not findings,
        "planHash": canonical_hash(material),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan competition repair recovery from registry/hash lineage evidence."
    )
    parser.add_argument(
        "--recovery",
        default="governance/competition_misrouted_fix_recovery_v1.json",
    )
    parser.add_argument(
        "--lineage-dir",
        default="dist/competition-lineage",
    )
    parser.add_argument(
        "--output",
        default="dist/competition-recovery-scope-plan.json",
    )
    parser.add_argument("--allow-findings", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    lineage_dir = Path(args.lineage_dir)
    plan = build_plan(
        recovery=read_object(Path(args.recovery)),
        registry=read_object(lineage_dir / "registry-snapshot.json"),
        graph=read_object(lineage_dir / "lineage-graph.json"),
        verification=read_object(lineage_dir / "verification-report.json"),
    )
    write_json(Path(args.output), plan)
    compact = {
        "verified": plan["verified"],
        "sourceCommit": plan["sourceCommit"],
        "runtimeHash": plan["runtimeHash"],
        "graphHash": plan["graphHash"],
        "planHash": plan["planHash"],
        "requirements": [
            {
                "id": item["id"],
                "disposition": item["disposition"],
                "structuralStatus": item["structuralStatus"],
                "candidateRuntimePathCount": len(item["candidateRuntimePaths"]),
                "missingRequiredFieldIds": item["missingRequiredFieldIds"],
            }
            for item in plan["requirements"]
        ],
        "findings": plan["findings"],
    }
    print(json.dumps(compact, ensure_ascii=False, sort_keys=True))
    return 0 if plan["verified"] or args.allow_findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
