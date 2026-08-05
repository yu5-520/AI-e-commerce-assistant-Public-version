"""V23 selected-module release receipt hard gate.

The gate proves that the exact verified release, selected module definitions, runners,
implementation files and active callable owners are identical between repository
validation, gray preflight and production startup. It never calls a model or mutates
business data.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping

RUNTIME_GATE_VERSION = "23.0.0-rc.1"
RUNTIME_CONFIG_PATH = Path("config/v23_registry_runtime.json")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_ENVIRONMENTS = {"repository_validation", "gray", "production"}
_BINDING_GRAY_MODE = "static_ast"
_BINDING_PRODUCTION_MODE = "dynamic_runtime"
_REQUIRED_TRUE_RULES = (
    "releaseManifestIdentityRequired",
    "runnerFileRequired",
    "runnerSymbolRequired",
    "implementationHashRequired",
    "activeBindingProbeRequired",
    "grayStaticBindingRequired",
    "productionDynamicBindingRequired",
    "moduleErrorIsolationRequired",
    "grayReceiptRequiredForProduction",
    "grayProductionReleaseIdentityMustMatch",
    "grayProductionModuleContractsMustMatch",
    "deploymentMustFailClosed",
)


class RegistryRuntimeGateError(RuntimeError):
    """Raised when the selected-module runtime gate cannot be evaluated safely."""


def project_root() -> Path:
    configured = str(os.getenv("AI_RELEASE_ROOT") or "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[2]
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _strings(values: Iterable[Any]) -> List[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _read_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RegistryRuntimeGateError(
            f"registry_gate_json_read_failed:{path}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise RegistryRuntimeGateError(f"registry_gate_json_object_required:{path}")
    return value


def _safe_release_path(root: Path, raw: str) -> Path:
    relative = Path(str(raw or ""))
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise RegistryRuntimeGateError(f"registry_gate_unsafe_release_path:{raw}")
    resolved = (root / relative).resolve()
    if root != resolved and root not in resolved.parents:
        raise RegistryRuntimeGateError(
            f"registry_gate_release_path_escapes_root:{raw}"
        )
    if resolved.is_symlink():
        raise RegistryRuntimeGateError(
            f"registry_gate_release_path_symlink_forbidden:{raw}"
        )
    return resolved


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _inside(candidate: Path, boundary: Path) -> bool:
    return candidate == boundary or boundary in candidate.parents


def _safe_output_path(
    root: Path,
    raw_path: Path,
    allowed_output_roots: Iterable[Path],
) -> Path:
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    candidate = _absolute_lexical(candidate)
    boundaries = [_absolute_lexical(root)]
    boundaries.extend(_absolute_lexical(path) for path in allowed_output_roots)
    if not any(_inside(candidate, boundary) for boundary in boundaries):
        raise RegistryRuntimeGateError(
            f"registry_gate_output_outside_allowed_roots:{candidate}"
        )
    return candidate


def _write_object(
    root: Path,
    raw_path: Path,
    value: Mapping[str, Any],
    allowed_output_roots: Iterable[Path],
) -> Path:
    target = _safe_output_path(root, raw_path, allowed_output_roots)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_runtime_projection(root: Path | None = None) -> Dict[str, Any]:
    release_root = (root or project_root()).resolve()
    config = _read_object(release_root / RUNTIME_CONFIG_PATH)
    if config.get("schema") != "registry.runtime_projection.v1":
        raise RegistryRuntimeGateError("registry_runtime_projection_schema_invalid")
    if config.get("version") != RUNTIME_GATE_VERSION:
        raise RegistryRuntimeGateError("registry_runtime_projection_version_invalid")
    if config.get("mode") != "selected_fail_closed":
        raise RegistryRuntimeGateError("registry_runtime_projection_mode_invalid")
    registry_root_hash = str(config.get("registryRootHash") or "")
    if not _HASH_RE.fullmatch(registry_root_hash):
        raise RegistryRuntimeGateError("registry_runtime_projection_root_hash_invalid")

    required_modules = _strings(config.get("requiredModules") or [])
    modules = dict(config.get("modules") or {})
    if not required_modules:
        raise RegistryRuntimeGateError("registry_runtime_projection_modules_empty")
    missing = [module_id for module_id in required_modules if module_id not in modules]
    if missing:
        raise RegistryRuntimeGateError(
            "registry_runtime_projection_module_missing:" + ",".join(missing)
        )

    rules = dict(config.get("rules") or {})
    disabled = [rule for rule in _REQUIRED_TRUE_RULES if rules.get(rule) is not True]
    if disabled:
        raise RegistryRuntimeGateError(
            "registry_runtime_projection_required_rules_disabled:"
            + ",".join(disabled)
        )
    if rules.get("activeBindingProbeRequired") is True and not any(
        isinstance(_dict(modules.get(module_id)).get("activeBindingProbe"), dict)
        and bool(_dict(modules.get(module_id)).get("activeBindingProbe"))
        for module_id in required_modules
    ):
        raise RegistryRuntimeGateError(
            "registry_runtime_projection_active_binding_probe_missing"
        )

    config["requiredModules"] = required_modules
    config["modules"] = modules
    config["rules"] = rules
    return config


def _release_manifest_identity(root: Path) -> Dict[str, str]:
    manifest = _read_object(root / "release" / "release-manifest.json")
    source_commit = str(manifest.get("sourceCommit") or "")
    release_hash = str(manifest.get("releaseHash") or "")
    manifest_hash = str(manifest.get("manifestHash") or "")
    if not _COMMIT_RE.fullmatch(source_commit):
        raise RegistryRuntimeGateError("registry_gate_manifest_source_commit_invalid")
    if not _HASH_RE.fullmatch(release_hash):
        raise RegistryRuntimeGateError("registry_gate_manifest_release_hash_invalid")
    if not _HASH_RE.fullmatch(manifest_hash):
        raise RegistryRuntimeGateError("registry_gate_manifest_hash_invalid")
    return {
        "sourceCommit": source_commit,
        "releaseHash": release_hash,
        "manifestHash": manifest_hash,
    }


def _runner_parts(runner: str) -> tuple[str, str, Path]:
    module_name, separator, symbol = str(runner or "").partition(":")
    if not separator or not module_name or not symbol:
        raise RegistryRuntimeGateError(f"registry_gate_runner_invalid:{runner}")
    path = Path(*module_name.split(".")).with_suffix(".py")
    return module_name, symbol, path


def _top_level_symbols(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        raise RegistryRuntimeGateError(
            f"registry_gate_runner_parse_failed:{path}:{exc}"
        ) from exc
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
    return symbols


def _module_path(module_name: str) -> str:
    return Path(*str(module_name or "").split(".")).with_suffix(".py").as_posix()


def _top_level_literal(path: Path, symbol: str) -> Any:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        raise RegistryRuntimeGateError(
            f"registry_gate_static_evidence_parse_failed:{path}:{exc}"
        ) from exc
    for node in tree.body:
        value_node = None
        targets = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value_node = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value_node = node.value
        if value_node is None:
            continue
        if any(isinstance(target, ast.Name) and target.id == symbol for target in targets):
            try:
                return ast.literal_eval(value_node)
            except Exception as exc:
                raise RegistryRuntimeGateError(
                    f"registry_gate_static_evidence_literal_invalid:{path}:{symbol}:{exc}"
                ) from exc
    raise RegistryRuntimeGateError(
        f"registry_gate_static_evidence_symbol_missing:{path}:{symbol}"
    )


def _selected_implementation_paths(projection: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    for module_id in projection.get("requiredModules") or []:
        definition = _dict(_dict(projection.get("modules")).get(module_id))
        paths.update(_strings(definition.get("implementationPaths") or []))
        runner = str(definition.get("runner") or "")
        if runner:
            try:
                _, _, relative = _runner_parts(runner)
            except RegistryRuntimeGateError:
                continue
            paths.add(relative.as_posix())
    return paths


def _binding_owner_map(result: Mapping[str, Any], expected: Mapping[str, Any]) -> Dict[str, str]:
    return {
        key: str(result.get(key) or "")
        for key in sorted(expected)
    }


def _binding_record(
    *,
    raw: Mapping[str, Any],
    verification_mode: str,
    result: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    expected = dict(raw.get("expectedOwners") or {})
    owner_map = _binding_owner_map(result, expected)
    owner_map_hash = _sha256_value(owner_map)
    material = {
        "module": str(raw.get("module") or ""),
        "symbol": str(raw.get("symbol") or ""),
        "expectedOwners": expected,
        "verificationMode": verification_mode,
        "ownerMap": owner_map,
        "ownerMapHash": owner_map_hash,
        "evidence": dict(evidence),
        "result": dict(result),
    }
    return {
        **material,
        "activeBindingHash": _sha256_value(material),
    }


def _static_active_binding_probe(
    definition: Mapping[str, Any],
    module_id: str,
    release_root: Path,
    selected_paths: set[str],
) -> tuple[Dict[str, Any] | None, List[str]]:
    raw = _dict(definition.get("activeBindingProbe"))
    if not raw:
        return None, []
    expected = dict(raw.get("expectedOwners") or {})
    static = _dict(raw.get("staticEvidence"))
    errors: List[str] = []
    if raw.get("grayMode") != _BINDING_GRAY_MODE:
        errors.append(f"registry_gate_gray_binding_mode_invalid:{module_id}")
    if raw.get("productionMode") != _BINDING_PRODUCTION_MODE:
        errors.append(f"registry_gate_production_binding_mode_invalid:{module_id}")
    module_name = str(static.get("module") or raw.get("module") or "").strip()
    probe_symbol = str(static.get("probeSymbol") or raw.get("symbol") or "").strip()
    owner_constant = str(static.get("ownerConstant") or "").strip()
    if not module_name or not probe_symbol or not owner_constant or not expected:
        errors.append(f"registry_gate_static_binding_definition_invalid:{module_id}")
    relative = _module_path(module_name) if module_name else ""
    path = None
    if relative and relative not in selected_paths:
        errors.append(
            f"registry_gate_static_binding_module_not_selected:{module_id}:{relative}"
        )
    elif relative:
        path = _safe_release_path(release_root, relative)
        if not path.is_file():
            errors.append(
                f"registry_gate_static_binding_module_missing:{module_id}:{relative}"
            )
        else:
            symbols = _top_level_symbols(path)
            if probe_symbol not in symbols:
                errors.append(
                    f"registry_gate_static_binding_probe_symbol_missing:{module_id}:{probe_symbol}"
                )
            try:
                constant = _top_level_literal(path, owner_constant)
            except RegistryRuntimeGateError as exc:
                errors.append(str(exc))
                constant = None
            if constant != expected:
                errors.append(
                    f"registry_gate_static_binding_owner_constant_mismatch:{module_id}"
                )
    owner_evidence: Dict[str, Any] = {}
    for key, owner in sorted(expected.items()):
        owner_relative = _module_path(str(owner))
        selected = owner_relative in selected_paths
        exists = False
        content_hash = None
        if selected:
            owner_path = _safe_release_path(release_root, owner_relative)
            exists = owner_path.is_file()
            if exists:
                content_hash = _sha256_file(owner_path)
        if not selected:
            errors.append(
                f"registry_gate_static_binding_owner_not_selected:{module_id}:{key}:{owner_relative}"
            )
        elif not exists:
            errors.append(
                f"registry_gate_static_binding_owner_missing:{module_id}:{key}:{owner_relative}"
            )
        owner_evidence[key] = {
            "module": owner,
            "path": owner_relative,
            "selected": selected,
            "exists": exists,
            "contentHash": content_hash,
        }
    result: Dict[str, Any] = {
        **expected,
        "matched": not errors,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "secondWorkerCreated": False,
        "verificationMode": _BINDING_GRAY_MODE,
    }
    record = _binding_record(
        raw=raw,
        verification_mode=_BINDING_GRAY_MODE,
        result=result,
        evidence={
            "staticEvidence": static,
            "ownerEvidence": owner_evidence,
            "businessRuntimeImported": False,
        },
    )
    return record, errors


def _dynamic_active_binding_probe(
    definition: Mapping[str, Any],
    module_id: str,
) -> tuple[Dict[str, Any] | None, List[str]]:
    raw = _dict(definition.get("activeBindingProbe"))
    if not raw:
        return None, []
    module_name = str(raw.get("module") or "").strip()
    symbol = str(raw.get("symbol") or "").strip()
    expected = dict(raw.get("expectedOwners") or {})
    errors: List[str] = []
    if raw.get("grayMode") != _BINDING_GRAY_MODE:
        errors.append(f"registry_gate_gray_binding_mode_invalid:{module_id}")
    if raw.get("productionMode") != _BINDING_PRODUCTION_MODE:
        errors.append(f"registry_gate_production_binding_mode_invalid:{module_id}")
    if not module_name or not symbol or not expected:
        return None, [
            *errors,
            f"registry_gate_active_binding_definition_invalid:{module_id}",
        ]
    try:
        module = importlib.import_module(module_name)
        probe = getattr(module, symbol)
        raw_result = probe()
    except Exception as exc:
        return None, [
            *errors,
            f"registry_gate_active_binding_probe_failed:{module_id}:{str(exc)[:300]}",
        ]
    if not isinstance(raw_result, dict):
        errors.append(
            f"registry_gate_active_binding_probe_result_invalid:{module_id}"
        )
        raw_result = {}
    result = dict(raw_result)
    result["verificationMode"] = _BINDING_PRODUCTION_MODE
    for key, value in expected.items():
        if result.get(key) != value:
            errors.append(
                f"registry_gate_active_binding_owner_mismatch:{module_id}:{key}"
            )
    if result.get("matched") is not True:
        errors.append(f"registry_gate_active_binding_not_matched:{module_id}")
    if result.get("databaseMutated") is not False:
        errors.append(
            f"registry_gate_active_binding_database_mutation:{module_id}"
        )
    if int(result.get("providerCallsExecuted") or 0) != 0:
        errors.append(f"registry_gate_active_binding_provider_call:{module_id}")
    if result.get("secondWorkerCreated") is not False:
        errors.append(f"registry_gate_active_binding_second_worker:{module_id}")
    record = _binding_record(
        raw=raw,
        verification_mode=_BINDING_PRODUCTION_MODE,
        result=result,
        evidence={"businessRuntimeImported": True},
    )
    return record, errors


def _execute_active_binding_probe(
    definition: Mapping[str, Any],
    module_id: str,
    *,
    environment: str,
    release_root: Path,
    selected_paths: set[str],
) -> tuple[Dict[str, Any] | None, List[str]]:
    if environment == "gray":
        return _static_active_binding_probe(
            definition,
            module_id,
            release_root,
            selected_paths,
        )
    return _dynamic_active_binding_probe(definition, module_id)


def build_selected_module_contracts(
    root: Path | None = None,
    *,
    environment: str = "repository_validation",
) -> Dict[str, Any]:
    release_root = (root or project_root()).resolve()
    environment_name = str(environment or "").strip().lower()
    if environment_name not in _ALLOWED_ENVIRONMENTS:
        raise RegistryRuntimeGateError(
            f"registry_gate_environment_invalid:{environment_name}"
        )
    projection = load_runtime_projection(release_root)
    selected_paths = _selected_implementation_paths(projection)
    contracts: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    for module_id in projection["requiredModules"]:
        module_errors: List[str] = []
        definition = dict(projection["modules"][module_id])
        runner = str(definition.get("runner") or "")
        _, symbol, runner_relative = _runner_parts(runner)
        runner_path = _safe_release_path(release_root, runner_relative.as_posix())
        runner_file_exists = runner_path.is_file()
        runner_symbol_exists = (
            symbol in _top_level_symbols(runner_path)
            if runner_file_exists
            else False
        )
        if not runner_file_exists:
            module_errors.append(
                f"registry_gate_runner_file_missing:{module_id}"
            )
        if not runner_symbol_exists:
            module_errors.append(
                f"registry_gate_runner_symbol_missing:{module_id}"
            )

        implementation_hashes: Dict[str, str | None] = {}
        for raw_path in _strings(definition.get("implementationPaths") or []):
            path = _safe_release_path(release_root, raw_path)
            if not path.is_file():
                implementation_hashes[raw_path] = None
                module_errors.append(
                    f"registry_gate_implementation_missing:{module_id}:{raw_path}"
                )
            else:
                implementation_hashes[raw_path] = _sha256_file(path)

        active_binding, binding_errors = _execute_active_binding_probe(
            definition,
            module_id,
            environment=environment_name,
            release_root=release_root,
            selected_paths=selected_paths,
        )
        module_errors.extend(binding_errors)
        binding_required = bool(_dict(definition.get("activeBindingProbe")))
        binding_status = (
            "not_required"
            if not binding_required
            else "verified"
            if active_binding is not None and not binding_errors
            else "invalid"
        )
        stable_material = {
            "registryRootHash": projection["registryRootHash"],
            "runtimeProjectionVersion": projection["version"],
            "moduleId": module_id,
            "definition": definition,
            "runnerFileExists": runner_file_exists,
            "runnerSymbolExists": runner_symbol_exists,
            "implementationContentHashes": implementation_hashes,
            "activeBindingOwnerMapHash": _dict(active_binding).get(
                "ownerMapHash"
            ),
        }
        contract = {
            **stable_material,
            "activeBindingProbe": active_binding,
            "moduleErrors": sorted(set(module_errors)),
            "loadStatus": "loaded" if not module_errors else "invalid",
            "bindingStatus": binding_status,
            "moduleContractHash": _sha256_value(stable_material),
        }
        contracts[module_id] = contract
        errors.extend(module_errors)

    root_material = {
        module_id: contract["moduleContractHash"]
        for module_id, contract in sorted(contracts.items())
    }
    return {
        "schema": "registry.selected_module_contracts.v1",
        "version": RUNTIME_GATE_VERSION,
        "environment": environment_name,
        "registryRootHash": projection["registryRootHash"],
        "runtimeProjectionHash": _sha256_value(projection),
        "requiredModules": projection["requiredModules"],
        "moduleContracts": contracts,
        "moduleContractRootHash": _sha256_value(root_material),
        "verified": not errors,
        "errors": sorted(set(errors)),
    }


def _receipt_material(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: receipt.get(key)
        for key in (
            "schema",
            "version",
            "environment",
            "releaseCommit",
            "releaseHash",
            "manifestHash",
            "capturedAt",
            "moduleId",
            "registryRootHash",
            "runtimeProjectionHash",
            "moduleContractRootHash",
            "moduleContractHash",
            "runner",
            "implementationContentHashes",
            "activeBindingProbe",
            "activeBindingHash",
            "activeBindingOwnerMapHash",
            "moduleErrors",
            "bindingStatus",
            "fieldIds",
            "schemaIds",
            "loadStatus",
            "source",
        )
    }


def _receipt_set_material(receipt_set: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: receipt_set.get(key)
        for key in (
            "schema",
            "version",
            "environment",
            "releaseCommit",
            "releaseHash",
            "manifestHash",
            "capturedAt",
            "registryRootHash",
            "runtimeProjectionHash",
            "moduleContractRootHash",
            "requiredModules",
            "receipts",
        )
    }


def build_runtime_receipt_set(
    root: Path | None = None,
    *,
    environment: str,
    release_commit: str,
    release_hash: str,
    captured_at: str | None = None,
    source: str = "selected_module_runtime_gate",
) -> Dict[str, Any]:
    release_root = (root or project_root()).resolve()
    environment_name = str(environment or "").strip().lower()
    if environment_name not in _ALLOWED_ENVIRONMENTS:
        raise RegistryRuntimeGateError(
            f"registry_gate_environment_invalid:{environment_name}"
        )
    manifest_identity = _release_manifest_identity(release_root)
    if release_commit != manifest_identity["sourceCommit"]:
        raise RegistryRuntimeGateError("registry_gate_release_commit_mismatch")
    if release_hash != manifest_identity["releaseHash"]:
        raise RegistryRuntimeGateError("registry_gate_release_hash_mismatch")

    contracts = build_selected_module_contracts(
        release_root, environment=environment_name
    )
    captured = str(captured_at or _utc_now())
    receipts: List[Dict[str, Any]] = []
    for module_id in contracts["requiredModules"]:
        contract = dict(contracts["moduleContracts"][module_id])
        definition = dict(contract.get("definition") or {})
        active_binding = contract.get("activeBindingProbe")
        receipt: Dict[str, Any] = {
            "schema": "registry.selected_module_receipt.v1",
            "version": RUNTIME_GATE_VERSION,
            "environment": environment_name,
            "releaseCommit": release_commit,
            "releaseHash": release_hash,
            "manifestHash": manifest_identity["manifestHash"],
            "capturedAt": captured,
            "moduleId": module_id,
            "registryRootHash": contracts["registryRootHash"],
            "runtimeProjectionHash": contracts["runtimeProjectionHash"],
            "moduleContractRootHash": contracts["moduleContractRootHash"],
            "moduleContractHash": contract["moduleContractHash"],
            "runner": definition.get("runner"),
            "implementationContentHashes": contract.get("implementationContentHashes"),
            "activeBindingProbe": active_binding,
            "activeBindingHash": _dict(active_binding).get("activeBindingHash"),
            "activeBindingOwnerMapHash": contract.get(
                "activeBindingOwnerMapHash"
            ),
            "moduleErrors": list(contract.get("moduleErrors") or []),
            "bindingStatus": contract.get("bindingStatus"),
            "fieldIds": _strings(definition.get("fieldIds") or []),
            "schemaIds": _strings(definition.get("schemaIds") or []),
            "loadStatus": contract.get("loadStatus"),
            "source": str(source or "selected_module_runtime_gate"),
        }
        receipt["receiptHash"] = _sha256_value(_receipt_material(receipt))
        receipts.append(receipt)

    receipt_set: Dict[str, Any] = {
        "schema": "registry.selected_module_receipt_set.v1",
        "version": RUNTIME_GATE_VERSION,
        "mode": "hard_gate",
        "environment": environment_name,
        "releaseCommit": release_commit,
        "releaseHash": release_hash,
        "manifestHash": manifest_identity["manifestHash"],
        "capturedAt": captured,
        "registryRootHash": contracts["registryRootHash"],
        "runtimeProjectionHash": contracts["runtimeProjectionHash"],
        "moduleContractRootHash": contracts["moduleContractRootHash"],
        "requiredModules": contracts["requiredModules"],
        "receipts": receipts,
        "contractErrors": contracts["errors"],
    }
    receipt_set["receiptSetHash"] = _sha256_value(
        _receipt_set_material(receipt_set)
    )
    return receipt_set


def load_receipt_set(path: Path) -> Dict[str, Any]:
    return _read_object(path)


def verify_runtime_receipt_set(
    receipt_set: Mapping[str, Any],
    root: Path | None = None,
    *,
    expected_environment: str,
    expected_release_commit: str,
    expected_release_hash: str,
) -> Dict[str, Any]:
    release_root = (root or project_root()).resolve()
    current = build_selected_module_contracts(
        release_root, environment=expected_environment
    )
    errors: List[str] = []
    if receipt_set.get("schema") != "registry.selected_module_receipt_set.v1":
        errors.append("registry_gate_receipt_set_schema_invalid")
    if receipt_set.get("environment") != expected_environment:
        errors.append("registry_gate_receipt_environment_mismatch")
    if receipt_set.get("releaseCommit") != expected_release_commit:
        errors.append("registry_gate_receipt_release_commit_mismatch")
    if receipt_set.get("releaseHash") != expected_release_hash:
        errors.append("registry_gate_receipt_release_hash_mismatch")
    if receipt_set.get("registryRootHash") != current["registryRootHash"]:
        errors.append("registry_gate_receipt_registry_root_mismatch")
    if receipt_set.get("runtimeProjectionHash") != current["runtimeProjectionHash"]:
        errors.append("registry_gate_receipt_projection_hash_mismatch")
    if receipt_set.get("moduleContractRootHash") != current["moduleContractRootHash"]:
        errors.append("registry_gate_receipt_module_root_mismatch")
    if receipt_set.get("receiptSetHash") != _sha256_value(
        _receipt_set_material(receipt_set)
    ):
        errors.append("registry_gate_receipt_set_hash_invalid")
    if current["verified"] is not True:
        errors.extend(current["errors"])

    indexed: MutableMapping[str, Dict[str, Any]] = {}
    receipt_errors: Dict[str, List[str]] = {}
    for raw in receipt_set.get("receipts") or []:
        if not isinstance(raw, dict):
            errors.append("registry_gate_receipt_record_invalid")
            continue
        module_id = str(raw.get("moduleId") or "")
        if not module_id:
            errors.append("registry_gate_receipt_module_id_missing")
            continue
        if module_id in indexed:
            errors.append(f"registry_gate_receipt_duplicate:{module_id}")
        indexed[module_id] = dict(raw)
        local: List[str] = []
        contract = dict(current["moduleContracts"].get(module_id) or {})
        if not contract:
            local.append("module_not_selected")
        else:
            definition = dict(contract.get("definition") or {})
            if raw.get("moduleContractHash") != contract.get("moduleContractHash"):
                local.append("module_contract_hash_mismatch")
            if raw.get("runner") != definition.get("runner"):
                local.append("runner_mismatch")
            if dict(raw.get("implementationContentHashes") or {}) != dict(
                contract.get("implementationContentHashes") or {}
            ):
                local.append("implementation_hash_mismatch")
            if dict(raw.get("activeBindingProbe") or {}) != dict(
                contract.get("activeBindingProbe") or {}
            ):
                local.append("active_binding_probe_mismatch")
            expected_binding_hash = _dict(
                contract.get("activeBindingProbe")
            ).get("activeBindingHash")
            if raw.get("activeBindingHash") != expected_binding_hash:
                local.append("active_binding_hash_mismatch")
            if raw.get("activeBindingOwnerMapHash") != contract.get(
                "activeBindingOwnerMapHash"
            ):
                local.append("active_binding_owner_map_hash_mismatch")
            if list(raw.get("moduleErrors") or []) != list(
                contract.get("moduleErrors") or []
            ):
                local.append("module_errors_mismatch")
            if raw.get("bindingStatus") != contract.get("bindingStatus"):
                local.append("binding_status_mismatch")
            if raw.get("loadStatus") != contract.get("loadStatus"):
                local.append("load_status_mismatch")
            if _strings(raw.get("fieldIds") or []) != _strings(
                definition.get("fieldIds") or []
            ):
                local.append("field_contract_mismatch")
            if _strings(raw.get("schemaIds") or []) != _strings(
                definition.get("schemaIds") or []
            ):
                local.append("schema_contract_mismatch")
        if raw.get("receiptHash") != _sha256_value(_receipt_material(raw)):
            local.append("receipt_hash_invalid")
        if raw.get("loadStatus") != "loaded":
            local.append("module_not_loaded")
        if local:
            receipt_errors[module_id] = local

    required = set(current["requiredModules"])
    missing = sorted(required - set(indexed))
    unexpected = sorted(set(indexed) - required)
    if missing:
        errors.append("registry_gate_required_receipts_missing:" + ",".join(missing))
    if unexpected:
        errors.append("registry_gate_unexpected_receipts:" + ",".join(unexpected))
    verified = not errors and not receipt_errors
    material = {
        "receiptSetHash": receipt_set.get("receiptSetHash"),
        "expectedEnvironment": expected_environment,
        "expectedReleaseCommit": expected_release_commit,
        "expectedReleaseHash": expected_release_hash,
        "moduleContractRootHash": current["moduleContractRootHash"],
        "errors": errors,
        "receiptErrors": receipt_errors,
    }
    return {
        "schema": "registry.selected_module_receipt_verification.v1",
        "version": RUNTIME_GATE_VERSION,
        "verified": verified,
        "status": "PASS" if verified else "BLOCK",
        "errors": errors,
        "receiptErrors": receipt_errors,
        "verificationHash": _sha256_value(material),
    }


def compare_gray_and_production(
    gray_receipt: Mapping[str, Any],
    production_receipt: Mapping[str, Any],
    root: Path | None = None,
) -> Dict[str, Any]:
    release_root = (root or project_root()).resolve()
    release_commit = str(production_receipt.get("releaseCommit") or "")
    release_hash = str(production_receipt.get("releaseHash") or "")
    gray_verification = verify_runtime_receipt_set(
        gray_receipt,
        release_root,
        expected_environment="gray",
        expected_release_commit=release_commit,
        expected_release_hash=release_hash,
    )
    production_verification = verify_runtime_receipt_set(
        production_receipt,
        release_root,
        expected_environment="production",
        expected_release_commit=release_commit,
        expected_release_hash=release_hash,
    )
    gray_index = {
        str(item.get("moduleId")): dict(item)
        for item in gray_receipt.get("receipts") or []
        if isinstance(item, dict) and item.get("moduleId")
    }
    production_index = {
        str(item.get("moduleId")): dict(item)
        for item in production_receipt.get("receipts") or []
        if isinstance(item, dict) and item.get("moduleId")
    }
    mismatches: Dict[str, List[str]] = {}
    for module_id in sorted(set(gray_index) | set(production_index)):
        local: List[str] = []
        gray = gray_index.get(module_id)
        production = production_index.get(module_id)
        if not gray:
            local.append("gray_receipt_missing")
        if not production:
            local.append("production_receipt_missing")
        if gray and production:
            for key in (
                "registryRootHash",
                "runtimeProjectionHash",
                "moduleContractRootHash",
                "moduleContractHash",
                "runner",
                "implementationContentHashes",
                "activeBindingOwnerMapHash",
                "moduleErrors",
                "bindingStatus",
                "loadStatus",
                "fieldIds",
                "schemaIds",
            ):
                if gray.get(key) != production.get(key):
                    local.append(f"{key}_mismatch")
            gray_binding = _dict(gray.get("activeBindingProbe"))
            production_binding = _dict(production.get("activeBindingProbe"))
            if gray_binding or production_binding:
                if gray_binding.get("verificationMode") != _BINDING_GRAY_MODE:
                    local.append("gray_binding_verification_mode_mismatch")
                if production_binding.get("verificationMode") != _BINDING_PRODUCTION_MODE:
                    local.append("production_binding_verification_mode_mismatch")
                if gray_binding.get("ownerMap") != production_binding.get("ownerMap"):
                    local.append("active_binding_owner_map_mismatch")
                if gray_binding.get("ownerMapHash") != production_binding.get("ownerMapHash"):
                    local.append("active_binding_owner_map_hash_mismatch")
        if local:
            mismatches[module_id] = local
    identity_match = bool(
        gray_receipt.get("releaseCommit") == production_receipt.get("releaseCommit")
        and gray_receipt.get("releaseHash") == production_receipt.get("releaseHash")
        and gray_receipt.get("manifestHash") == production_receipt.get("manifestHash")
    )
    passed = bool(
        identity_match
        and gray_verification["verified"] is True
        and production_verification["verified"] is True
        and not mismatches
    )
    material = {
        "grayVerificationHash": gray_verification["verificationHash"],
        "productionVerificationHash": production_verification["verificationHash"],
        "identityMatch": identity_match,
        "mismatches": mismatches,
    }
    return {
        "schema": "registry.selected_module_environment_comparison.v1",
        "version": RUNTIME_GATE_VERSION,
        "passed": passed,
        "status": "PASS" if passed else "BLOCK",
        "identityMatch": identity_match,
        "grayVerification": gray_verification,
        "productionVerification": production_verification,
        "mismatches": mismatches,
        "comparisonHash": _sha256_value(material),
    }


def run_startup_gate(
    root: Path | None = None,
    *,
    environment: str,
    release_commit: str,
    release_hash: str,
    receipt_output: Path,
    report_output: Path,
    gray_receipt_path: Path | None = None,
    allowed_output_roots: Iterable[Path] = (),
    captured_at: str | None = None,
    source: str = "selected_module_runtime_gate",
) -> Dict[str, Any]:
    release_root = (root or project_root()).resolve()
    receipt_set = build_runtime_receipt_set(
        release_root,
        environment=environment,
        release_commit=release_commit,
        release_hash=release_hash,
        captured_at=captured_at,
        source=source,
    )
    receipt_target = _write_object(
        release_root,
        receipt_output,
        receipt_set,
        allowed_output_roots,
    )
    verification = verify_runtime_receipt_set(
        receipt_set,
        release_root,
        expected_environment=environment,
        expected_release_commit=release_commit,
        expected_release_hash=release_hash,
    )
    comparison: Dict[str, Any] | None = None
    if environment == "production":
        if not gray_receipt_path or not gray_receipt_path.is_file():
            comparison = {
                "passed": False,
                "status": "BLOCK",
                "mismatches": {"__gate__": ["gray_receipt_required"]},
                "comparisonHash": _sha256_value({"grayReceiptRequired": True}),
            }
        else:
            comparison = compare_gray_and_production(
                load_receipt_set(gray_receipt_path),
                receipt_set,
                release_root,
            )
    passed = bool(
        verification["verified"] is True
        and (
            environment != "production"
            or comparison
            and comparison.get("passed") is True
        )
    )
    failures: List[str] = []
    if verification["verified"] is not True:
        failures.append("selected_module_receipt_verification_failed")
    if environment == "production" and not (
        comparison and comparison.get("passed") is True
    ):
        failures.append("gray_production_selected_module_parity_failed")
    material = {
        "environment": environment,
        "releaseCommit": release_commit,
        "releaseHash": release_hash,
        "receiptSetHash": receipt_set["receiptSetHash"],
        "verificationHash": verification["verificationHash"],
        "comparisonHash": (comparison or {}).get("comparisonHash"),
        "failures": failures,
    }
    report: Dict[str, Any] = {
        "schema": "registry.selected_module_startup_gate.v1",
        "version": RUNTIME_GATE_VERSION,
        "mode": "hard_gate",
        "environment": environment,
        "releaseCommit": release_commit,
        "releaseHash": release_hash,
        "hardGateStatus": "PASS" if passed else "BLOCK",
        "hardGatePassed": passed,
        "deploymentBlocked": not passed,
        "receiptPath": str(receipt_target),
        "receiptSetHash": receipt_set["receiptSetHash"],
        "verification": verification,
        "environmentComparison": comparison,
        "failures": failures,
        "hardGateHash": _sha256_value(material),
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }
    _write_object(release_root, report_output, report, allowed_output_roots)
    return report


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the V23 selected-module gray/production receipt hard gate."
    )
    parser.add_argument(
        "--environment",
        required=True,
        choices=["repository_validation", "gray", "production"],
    )
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--release-hash", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--gray-receipt")
    parser.add_argument("--allowed-output-root", action="append", default=[])
    parser.add_argument("--captured-at")
    parser.add_argument("--source", default="selected_module_runtime_gate")
    args = parser.parse_args(argv)

    report = run_startup_gate(
        project_root(),
        environment=args.environment,
        release_commit=args.release_commit,
        release_hash=args.release_hash,
        receipt_output=Path(args.output),
        report_output=Path(args.report),
        gray_receipt_path=Path(args.gray_receipt) if args.gray_receipt else None,
        allowed_output_roots=[Path(path) for path in args.allowed_output_root],
        captured_at=args.captured_at,
        source=args.source,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["hardGatePassed"] is True else 4


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RUNTIME_GATE_VERSION",
    "build_selected_module_contracts",
    "build_runtime_receipt_set",
    "compare_gray_and_production",
    "load_receipt_set",
    "load_runtime_projection",
    "run_startup_gate",
    "verify_runtime_receipt_set",
]
