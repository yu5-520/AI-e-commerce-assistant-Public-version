#!/usr/bin/env python3
"""External-interface and operator-boundary gate for the competition runtime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _path_matches(relative: str, configured: str) -> bool:
    clean = configured.rstrip("/")
    return relative == clean or relative.startswith(clean + "/")


def compile_interface_governance(
    root: Path,
    *,
    scope: Mapping[str, Any],
    runtime_paths: Sequence[str] | set[str],
) -> dict[str, Any]:
    findings: list[str] = []
    warnings: list[str] = []
    runtime_set = {str(item) for item in runtime_paths}
    boundary_path = root / str(scope.get("productBoundaryPath") or "")
    registry_path = root / str(scope.get("externalInterfaceRegistryPath") or "")
    if not boundary_path.is_file():
        findings.append(f"COMPETITION_PRODUCT_BOUNDARY_MISSING:{boundary_path}")
        boundary: dict[str, Any] = {}
    else:
        boundary = _read(boundary_path)
    if not registry_path.is_file():
        findings.append(f"EXTERNAL_INTERFACE_REGISTRY_MISSING:{registry_path}")
        registry: dict[str, Any] = {"interfaces": {}}
    else:
        registry = _read(registry_path)

    for field in (
        "applicationLoginEnabled",
        "applicationAccountSystemEnabled",
        "roleSwitchEnabled",
        "tenantManagementEnabled",
        "clientIdentityOverrideAllowed",
    ):
        if boundary.get(field) is not False:
            findings.append(f"PRODUCT_BOUNDARY_REQUIRES_FALSE:{field}")
    actor = boundary.get("fixedActor") or {}
    if boundary.get("runtimeActorMode") != "fixed_competition_operator":
        findings.append("FIXED_OPERATOR_MODE_REQUIRED")
    if actor.get("actorId") != "competition_operator" or actor.get("role") != "operator":
        findings.append("FIXED_OPERATOR_IDENTITY_INVALID")
    if actor.get("serverInjected") is not True or actor.get("clientOverrideAllowed") is not False:
        findings.append("FIXED_OPERATOR_SERVER_BOUNDARY_INVALID")
    boundary_material = {key: value for key, value in boundary.items() if key != "boundaryHash"}
    boundary_hash = _canonical_hash(boundary_material)
    if boundary.get("boundaryHash") != boundary_hash:
        findings.append("PRODUCT_BOUNDARY_HASH_MISMATCH")

    interfaces = registry.get("interfaces")
    if not isinstance(interfaces, dict):
        findings.append("EXTERNAL_INTERFACE_OBJECT_REQUIRED")
        interfaces = {}
    registry_material = {key: value for key, value in registry.items() if key != "registryHash"}
    registry_hash = _canonical_hash(registry_material)
    if registry.get("registryHash") != registry_hash:
        findings.append("EXTERNAL_INTERFACE_REGISTRY_HASH_MISMATCH")

    nodes: list[dict[str, Any]] = []
    edges: list[tuple[str, str, str]] = []
    enabled_adapter_paths: set[str] = set()
    enabled_count = 0
    disabled_count = 0
    for interface_id in sorted(interfaces):
        raw = interfaces.get(interface_id)
        if not isinstance(raw, dict):
            findings.append(f"EXTERNAL_INTERFACE_RECORD_INVALID:{interface_id}")
            continue
        material = {key: value for key, value in raw.items() if key != "contractHash"}
        contract_hash = _canonical_hash(material)
        if raw.get("contractHash") != contract_hash:
            findings.append(f"EXTERNAL_INTERFACE_CONTRACT_HASH_MISMATCH:{interface_id}")
        execution_enabled = raw.get("executionEnabled") is True
        binding_present = raw.get("bindingPresent") is True
        interface_available = raw.get("interfaceAvailable") is True
        adapter_paths = [str(item) for item in raw.get("adapterPaths") or []]
        implementation_hashes = raw.get("implementationHashes") or {}
        if execution_enabled:
            enabled_count += 1
            if not interface_available or not binding_present:
                findings.append(f"ENABLED_INTERFACE_BINDING_INVALID:{interface_id}")
            if not adapter_paths:
                findings.append(f"ENABLED_INTERFACE_ADAPTER_REQUIRED:{interface_id}")
            for relative in adapter_paths:
                path = root / relative
                if not path.is_file():
                    findings.append(f"INTERFACE_ADAPTER_MISSING:{interface_id}:{relative}")
                    continue
                if relative not in runtime_set:
                    findings.append(f"ENABLED_INTERFACE_OUTSIDE_RUNTIME_LINEAGE:{interface_id}:{relative}")
                actual_hash = _file_hash(path)
                if implementation_hashes.get(relative) != actual_hash:
                    findings.append(f"INTERFACE_IMPLEMENTATION_HASH_MISMATCH:{interface_id}:{relative}")
                enabled_adapter_paths.add(relative)
                edges.append((f"interface:{interface_id}", f"file:{relative}", "IMPLEMENTED_BY"))
            if raw.get("networkEgress") is True and not raw.get("allowedHosts"):
                findings.append(f"NETWORK_INTERFACE_HOST_ALLOWLIST_REQUIRED:{interface_id}")
            for module_id in raw.get("upstreamRegistryModules") or []:
                edges.append((f"registry:{module_id}", f"interface:{interface_id}", "USES_INTERFACE"))
        else:
            disabled_count += 1
            if binding_present:
                findings.append(f"DISABLED_INTERFACE_BINDING_PRESENT:{interface_id}")
            if raw.get("competitionStatus") == "PUBLIC_RUNTIME":
                findings.append(f"PUBLIC_RUNTIME_INTERFACE_DISABLED:{interface_id}")
        nodes.append({
            "id": f"interface:{interface_id}",
            "type": "external_interface",
            "interfaceId": interface_id,
            "capability": raw.get("capability"),
            "provider": raw.get("provider"),
            "executionEnabled": execution_enabled,
            "contractHash": contract_hash,
        })

    policy_literal_paths = {
        str(scope.get("productBoundaryPath") or ""),
        str(scope.get("externalInterfaceRegistryPath") or ""),
        "config/competition_runtime_scope.json",
    }
    for relative in sorted(runtime_set):
        for forbidden in scope.get("forbiddenRuntimePaths") or []:
            if _path_matches(relative, str(forbidden)):
                findings.append(f"FORBIDDEN_RUNTIME_PATH:{relative}")
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in {".py", ".js", ".html", ".css", ".json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if relative not in policy_literal_paths:
            for token in scope.get("forbiddenRuntimeContent") or []:
                if str(token) in text:
                    findings.append(f"FORBIDDEN_RUNTIME_CONTENT:{relative}:{token}")
        if relative.startswith("src/") and relative.endswith(".py"):
            markers = [str(item) for item in scope.get("networkCallMarkers") or []]
            if any(marker in text for marker in markers) and relative not in enabled_adapter_paths:
                findings.append(f"UNREGISTERED_NETWORK_CALL:{relative}")

    material = {
        "schema": "competition.interface_governance.v1",
        "productBoundaryPath": str(scope.get("productBoundaryPath") or ""),
        "productBoundaryHash": boundary_hash,
        "externalInterfaceRegistryPath": str(scope.get("externalInterfaceRegistryPath") or ""),
        "externalInterfaceRegistryHash": registry_hash,
        "enabledInterfaceCount": enabled_count,
        "disabledInterfaceCount": disabled_count,
        "enabledAdapterPaths": sorted(enabled_adapter_paths),
        "nodes": nodes,
        "edges": [
            {"from": source, "to": target, "type": edge_type}
            for source, target, edge_type in sorted(set(edges))
        ],
        "findings": sorted(set(findings)),
        "warnings": sorted(set(warnings)),
    }
    return {**material, "verified": not findings, "governanceHash": _canonical_hash(material)}
