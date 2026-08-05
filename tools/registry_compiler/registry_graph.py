"""Deterministic dependency and impact graph for the V23 registry.

The graph is report-only in V23.0.0-alpha.2.  It calculates which registered
business modules are directly or transitively affected by a field, Schema, module,
interface, or station change.  It does not mutate runtime code or deployment state.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Set

from .compile_registry import load_registry_documents, sha256_value

REGISTRY_GRAPH_VERSION = "23.0.0-alpha.2"


def _records(document: Mapping[str, Any], key: str, identity: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in document.get(key) or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get(identity) or "").strip()
        if item_id:
            result[item_id] = dict(raw)
    return result


def _sorted_set(values: Iterable[str]) -> List[str]:
    return sorted({str(value) for value in values if str(value)})


def build_dependency_graph(root: Path | None = None) -> Dict[str, Any]:
    documents = load_registry_documents(root)
    fields = _records(documents["fields.json"], "fields", "fieldId")
    schemas = _records(documents["schemas.json"], "schemas", "schemaId")
    modules = _records(documents["modules.json"], "modules", "moduleId")
    interfaces = _records(documents["interfaces.json"], "interfaces", "interfaceId")
    stations = _records(documents["stations.json"], "stations", "stationId")

    field_to_modules: MutableMapping[str, Set[str]] = {field_id: set() for field_id in fields}
    schema_to_modules: MutableMapping[str, Set[str]] = {schema_id: set() for schema_id in schemas}
    interface_to_modules: MutableMapping[str, Set[str]] = {}
    station_to_modules: MutableMapping[str, Set[str]] = {}
    upstream: MutableMapping[str, Set[str]] = {module_id: set() for module_id in modules}
    downstream: MutableMapping[str, Set[str]] = {module_id: set() for module_id in modules}

    for field_id, field in fields.items():
        for module_id in [
            field.get("ownerModule"),
            *(field.get("readers") or []),
            *(field.get("writers") or []),
        ]:
            if str(module_id or "") in modules:
                field_to_modules[field_id].add(str(module_id))

    for module_id, module in modules.items():
        for field_id in [*(module.get("reads") or []), *(module.get("writes") or [])]:
            if str(field_id) in fields:
                field_to_modules.setdefault(str(field_id), set()).add(module_id)
        for schema_id in [*(module.get("inputSchemas") or []), *(module.get("outputSchemas") or [])]:
            if str(schema_id) in schemas:
                schema_to_modules.setdefault(str(schema_id), set()).add(module_id)
        for parent in module.get("upstream") or []:
            parent_id = str(parent)
            if parent_id in modules:
                upstream[module_id].add(parent_id)
                downstream[parent_id].add(module_id)
        for child in module.get("downstream") or []:
            child_id = str(child)
            if child_id in modules:
                downstream[module_id].add(child_id)
                upstream[child_id].add(module_id)

    for schema_id, schema in schemas.items():
        owner = str(schema.get("ownerModule") or "")
        if owner in modules:
            schema_to_modules.setdefault(schema_id, set()).add(owner)
        for field_id in [*(schema.get("requiredFields") or []), *(schema.get("optionalFields") or [])]:
            if str(field_id) in fields:
                for module_id in schema_to_modules.get(schema_id, set()):
                    field_to_modules.setdefault(str(field_id), set()).add(module_id)

    for interface_id, interface in interfaces.items():
        owners: Set[str] = set()
        owner = str(interface.get("ownerModule") or "")
        if owner in modules:
            owners.add(owner)
        interface_to_modules[interface_id] = owners

    for station_id, station in stations.items():
        owners: Set[str] = set()
        module_id = str(station.get("moduleId") or "")
        if module_id in modules:
            owners.add(module_id)
        station_to_modules[station_id] = owners

    graph_material = {
        "fields": {key: _sorted_set(value) for key, value in sorted(field_to_modules.items())},
        "schemas": {key: _sorted_set(value) for key, value in sorted(schema_to_modules.items())},
        "interfaces": {key: _sorted_set(value) for key, value in sorted(interface_to_modules.items())},
        "stations": {key: _sorted_set(value) for key, value in sorted(station_to_modules.items())},
        "upstream": {key: _sorted_set(value) for key, value in sorted(upstream.items())},
        "downstream": {key: _sorted_set(value) for key, value in sorted(downstream.items())},
    }
    return {
        "schema": "registry.dependency_graph.v1",
        "version": REGISTRY_GRAPH_VERSION,
        "mode": "report_only",
        "graphHash": sha256_value(graph_material),
        **graph_material,
    }


def _downstream_closure(seeds: Iterable[str], downstream: Mapping[str, Iterable[str]]) -> Set[str]:
    visited: Set[str] = set()
    queue = deque(str(seed) for seed in seeds if str(seed))
    while queue:
        module_id = queue.popleft()
        if module_id in visited:
            continue
        visited.add(module_id)
        for child in downstream.get(module_id) or []:
            if str(child) not in visited:
                queue.append(str(child))
    return visited


def calculate_impact(
    root: Path | None = None,
    *,
    changed_fields: Iterable[str] = (),
    changed_schemas: Iterable[str] = (),
    changed_modules: Iterable[str] = (),
    changed_interfaces: Iterable[str] = (),
    changed_stations: Iterable[str] = (),
) -> Dict[str, Any]:
    graph = build_dependency_graph(root)
    documents = load_registry_documents(root)
    modules = _records(documents["modules.json"], "modules", "moduleId")

    seeds: Set[str] = set()
    reasons: MutableMapping[str, Set[str]] = {}

    def add(module_ids: Iterable[str], reason: str) -> None:
        for raw in module_ids:
            module_id = str(raw)
            if module_id not in modules:
                continue
            seeds.add(module_id)
            reasons.setdefault(module_id, set()).add(reason)

    normalized_fields = _sorted_set(changed_fields)
    normalized_schemas = _sorted_set(changed_schemas)
    normalized_modules = _sorted_set(changed_modules)
    normalized_interfaces = _sorted_set(changed_interfaces)
    normalized_stations = _sorted_set(changed_stations)

    for field_id in normalized_fields:
        add(graph["fields"].get(field_id) or [], f"field:{field_id}")
    for schema_id in normalized_schemas:
        add(graph["schemas"].get(schema_id) or [], f"schema:{schema_id}")
    for module_id in normalized_modules:
        add([module_id], f"module:{module_id}")
    for interface_id in normalized_interfaces:
        add(graph["interfaces"].get(interface_id) or [], f"interface:{interface_id}")
    for station_id in normalized_stations:
        add(graph["stations"].get(station_id) or [], f"station:{station_id}")

    affected = _downstream_closure(seeds, graph["downstream"])
    direct = set(seeds)
    transitive = affected - direct
    unaffected = set(modules) - affected

    material = {
        "changes": {
            "fields": normalized_fields,
            "schemas": normalized_schemas,
            "modules": normalized_modules,
            "interfaces": normalized_interfaces,
            "stations": normalized_stations,
        },
        "directAffectedModules": sorted(direct),
        "transitiveAffectedModules": sorted(transitive),
        "theoreticalAffectedModules": sorted(affected),
        "unaffectedModules": sorted(unaffected),
        "reasons": {key: sorted(value) for key, value in sorted(reasons.items())},
        "graphHash": graph["graphHash"],
    }
    return {
        "schema": "registry.impact.v1",
        "version": REGISTRY_GRAPH_VERSION,
        "mode": "report_only",
        "impactHash": sha256_value(material),
        **material,
    }
