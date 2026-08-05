"""Soft-gate update completeness reports for V23 beta.1."""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Set

from .change_manifest import validate_change_manifest
from .compile_registry import sha256_value
from .module_contracts import build_module_contracts
from .registry_graph import calculate_impact

COMPLETENESS_VERSION = "23.0.0-beta.1"
_GOVERNANCE_PREFIXES = (
    "tools/registry_compiler/",
    "contracts/registry/",
    "contracts/changes/",
)


def git_changed_paths(
    root: Path | None = None, *, base_ref: str, head_ref: str = "HEAD"
) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    try:
        completed = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
            cwd=str(repository),
            check=True,
            capture_output=True,
            text=True,
        )
        paths = sorted(
            {
                line.strip().replace("\\", "/")
                for line in completed.stdout.splitlines()
                if line.strip()
            }
        )
        return {"resolved": True, "paths": paths, "error": None}
    except Exception as exc:
        return {"resolved": False, "paths": [], "error": str(exc)}


def _path_module_index(module_contracts: Mapping[str, Any]) -> Dict[str, Set[str]]:
    index: MutableMapping[str, Set[str]] = {}
    for module_id, contract in dict(module_contracts.get("moduleContracts") or {}).items():
        runner_path = str(contract.get("runnerPath") or "").replace("\\", "/")
        if runner_path:
            index.setdefault(runner_path, set()).add(str(module_id))
    return dict(index)


def map_changed_paths_to_modules(
    changed_paths: Iterable[str],
    module_contracts: Mapping[str, Any],
    path_module_hints: Mapping[str, Iterable[str]] | None = None,
) -> Dict[str, Any]:
    exact_index = _path_module_index(module_contracts)
    hints = {
        str(pattern).replace("\\", "/"): {str(module_id) for module_id in module_ids}
        for pattern, module_ids in dict(path_module_hints or {}).items()
    }
    path_modules: Dict[str, List[str]] = {}
    unowned: List[str] = []
    for raw_path in changed_paths:
        path = str(raw_path).strip().replace("\\", "/")
        if not path:
            continue
        owners: Set[str] = set(exact_index.get(path) or set())
        if any(path.startswith(prefix) for prefix in _GOVERNANCE_PREFIXES):
            owners.add("registry_compiler")
        for pattern, module_ids in hints.items():
            if fnmatch.fnmatch(path, pattern):
                owners.update(module_ids)
        if owners:
            path_modules[path] = sorted(owners)
        else:
            unowned.append(path)
    actual_modules = sorted(
        {module_id for owners in path_modules.values() for module_id in owners}
    )
    return {
        "pathModules": path_modules,
        "actualChangedModules": actual_modules,
        "unownedChangedPaths": sorted(set(unowned)),
    }


def build_completeness_report(
    manifest: Mapping[str, Any],
    root: Path | None = None,
    *,
    changed_paths_override: Iterable[str] | None = None,
) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    validation = validate_change_manifest(manifest, repository)
    normalized = dict(validation.get("manifest") or {})
    changes = dict(normalized.get("changes") or {})

    impact = calculate_impact(
        repository,
        changed_fields=changes.get("fields") or [],
        changed_schemas=changes.get("schemas") or [],
        changed_modules=changes.get("modules") or [],
        changed_interfaces=changes.get("interfaces") or [],
        changed_stations=changes.get("stations") or [],
    )
    contracts = build_module_contracts(repository)
    changed_paths = sorted(
        {
            str(path).strip().replace("\\", "/")
            for path in (
                changed_paths_override
                if changed_paths_override is not None
                else normalized.get("changedPaths") or []
            )
            if str(path).strip()
        }
    )
    mapping = map_changed_paths_to_modules(
        changed_paths,
        contracts,
        normalized.get("pathModuleHints") or {},
    )

    theoretical = set(impact.get("theoreticalAffectedModules") or [])
    direct = set(impact.get("directAffectedModules") or [])
    actual = set(mapping.get("actualChangedModules") or [])
    required = set(normalized.get("expectedImplementationModules") or []) or direct
    approved_no_code = set(normalized.get("approvedNoCodeChangeModules") or [])

    missing_required = sorted(required - actual)
    unexpected_changed = sorted(actual - theoretical)
    unverified_affected = sorted(theoretical - actual - approved_no_code)
    approved_outside_impact = sorted(approved_no_code - theoretical)

    module_hashes = {
        module_id: dict(contracts.get("moduleContracts") or {}).get(module_id, {}).get(
            "moduleContractHash"
        )
        for module_id in sorted(actual | required | approved_no_code)
    }
    approval_status = str(
        dict(normalized.get("approval") or {}).get("status") or "DRAFT"
    )
    soft_gate_passed = bool(
        validation.get("valid") is True
        and approval_status == "APPROVED"
        and not missing_required
        and not unexpected_changed
        and not unverified_affected
        and not approved_outside_impact
    )

    material = {
        "changeManifestHash": validation.get("changeManifestHash"),
        "impactHash": impact.get("impactHash"),
        "moduleContractRootHash": contracts.get("moduleContractRootHash"),
        "changedPaths": changed_paths,
        "actualChangedModules": sorted(actual),
        "requiredChangedModules": sorted(required),
        "approvedNoCodeChangeModules": sorted(approved_no_code),
        "missingRequiredChanges": missing_required,
        "unexpectedChangedModules": unexpected_changed,
        "unverifiedAffectedModules": unverified_affected,
        "moduleContractHashes": module_hashes,
    }
    completeness_hash = sha256_value(material)
    return {
        "schema": "registry.update_completeness.v1",
        "version": COMPLETENESS_VERSION,
        "mode": "soft_gate",
        "changeId": normalized.get("changeId"),
        "softGateStatus": "PASS" if soft_gate_passed else "WARN",
        "softGatePassed": soft_gate_passed,
        "deploymentBlocked": False,
        "validation": validation,
        "impact": impact,
        "pathMapping": mapping,
        "moduleContracts": contracts,
        "requiredChangedModules": sorted(required),
        "missingRequiredChanges": missing_required,
        "unexpectedChangedModules": unexpected_changed,
        "unverifiedAffectedModules": unverified_affected,
        "approvedNoCodeOutsideImpact": approved_outside_impact,
        "moduleContractHashes": module_hashes,
        "hashLineage": {
            "registryRootHash": contracts.get("registryRootHash"),
            "changeManifestHash": validation.get("changeManifestHash"),
            "graphHash": impact.get("graphHash"),
            "impactHash": impact.get("impactHash"),
            "moduleContractRootHash": contracts.get("moduleContractRootHash"),
            "completenessHash": completeness_hash,
        },
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }
