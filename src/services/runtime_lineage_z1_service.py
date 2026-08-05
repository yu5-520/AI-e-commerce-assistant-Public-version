"""Z1.0 active runtime registration and lineage acceptance.

This module is intentionally stdlib-only. It discovers the repository's mounted FastAPI
routes, ACTIVE registry runners, explicit workers, scheduler loops, CLI entries and server
identity without importing the business runtime, calling a provider or mutating data.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

RUNTIME_LINEAGE_VERSION = "Z1.0.4"
REGISTRATION_PATH = Path("contracts/runtime/active-runtime-registration.json")
MODULE_REGISTRY_PATH = Path("contracts/registry/modules.json")
INTERFACE_REGISTRY_PATH = Path("contracts/registry/interfaces.json")
RUNTIME_PROJECTION_PATH = Path("config/v23_registry_runtime.json")
_HTTP_METHODS = {
    "get": ["GET"],
    "post": ["POST"],
    "put": ["PUT"],
    "patch": ["PATCH"],
    "delete": ["DELETE"],
    "head": ["HEAD"],
    "options": ["OPTIONS"],
}


class RuntimeLineageError(RuntimeError):
    """Raised when runtime discovery or acceptance cannot be evaluated safely."""


def project_root(root: Path | None = None) -> Path:
    if root is not None:
        return root.resolve()
    configured = str(os.getenv("AI_RELEASE_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _read_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeLineageError(f"runtime_lineage_json_read_failed:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeLineageError(f"runtime_lineage_json_object_required:{path}")
    return value


def _strings(values: Iterable[Any]) -> List[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _literal_strings(node: ast.AST | None) -> List[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [
            value
            for item in node.elts
            for value in [_literal_string(item)]
            if value is not None
        ]
    value = _literal_string(node)
    return [value] if value is not None else []


def _join_http_path(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path:
        return prefix
    return prefix.rstrip("/") + "/" + path.lstrip("/")


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        raise RuntimeLineageError(f"runtime_lineage_python_parse_failed:{path}:{exc}") from exc


def _router_prefix(tree: ast.Module) -> str:
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not any(isinstance(target, ast.Name) and target.id == "router" for target in targets):
            continue
        if not isinstance(value, ast.Call):
            continue
        function = value.func
        name = function.id if isinstance(function, ast.Name) else None
        if name != "APIRouter":
            continue
        for keyword in value.keywords:
            if keyword.arg == "prefix":
                return _literal_string(keyword.value) or ""
    return ""


def _decorator_methods(call: ast.Call, method_name: str) -> List[str]:
    if method_name in _HTTP_METHODS:
        return list(_HTTP_METHODS[method_name])
    if method_name != "api_route":
        return []
    for keyword in call.keywords:
        if keyword.arg == "methods":
            return [value.upper() for value in _literal_strings(keyword.value)]
    return ["GET"]


def _has_explicit_error_path(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            return True
        if isinstance(child, ast.Call):
            function = child.func
            if isinstance(function, ast.Name) and function.id == "HTTPException":
                return True
            if isinstance(function, ast.Attribute) and function.attr == "HTTPException":
                return True
    return False


def _discover_routes(
    *,
    source_path: Path,
    module_name: str,
    decorator_owner: str,
    prefix: str,
) -> List[Dict[str, Any]]:
    tree = _parse(source_path)
    entries: List[Dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            function = decorator.func
            if not isinstance(function, ast.Attribute):
                continue
            if not isinstance(function.value, ast.Name) or function.value.id != decorator_owner:
                continue
            methods = _decorator_methods(decorator, function.attr)
            if not methods:
                continue
            route_path = _literal_string(decorator.args[0]) if decorator.args else None
            if route_path is None:
                continue
            full_path = _join_http_path(prefix, route_path)
            error_mode = (
                "explicit_http_exception"
                if _has_explicit_error_path(node)
                else "fastapi_exception_boundary"
            )
            for method in methods:
                entries.append(
                    {
                        "runtimeId": f"http:{method}:{full_path}",
                        "kind": "http",
                        "method": method,
                        "path": full_path,
                        "entry": f"{module_name}:{node.name}",
                        "sourcePath": source_path.as_posix(),
                        "errorOwner": module_name,
                        "errorPathMode": error_mode,
                    }
                )
    return entries


def _mounted_router_modules(main_path: Path) -> List[str]:
    tree = _parse(main_path)
    mounted: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "route_module":
            continue
        if not isinstance(node.iter, (ast.List, ast.Tuple)):
            continue
        for item in node.iter.elts:
            if isinstance(item, ast.Name):
                mounted.add(item.id)
    return sorted(mounted)


def _top_level_symbols(path: Path) -> set[str]:
    tree = _parse(path)
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
    return symbols


def _entry_parts(entry: str) -> tuple[str, str, Path]:
    module_name, separator, symbol = str(entry or "").partition(":")
    if not separator or not module_name or not symbol:
        raise RuntimeLineageError(f"runtime_lineage_entry_invalid:{entry}")
    relative = Path(*module_name.split(".")).with_suffix(".py")
    return module_name, symbol, relative


def _validate_python_entry(
    *,
    root: Path,
    entry: str,
    finding_prefix: str,
) -> tuple[Dict[str, Any], List[str]]:
    findings: List[str] = []
    try:
        module_name, symbol, relative = _entry_parts(entry)
    except RuntimeLineageError as exc:
        return {"entry": entry, "exists": False, "symbolExists": False}, [str(exc)]
    path = root / relative
    exists = path.is_file()
    symbol_exists = False
    if not exists:
        findings.append(f"{finding_prefix}_MODULE_MISSING:{entry}:{relative.as_posix()}")
    else:
        try:
            symbol_exists = symbol in _top_level_symbols(path)
        except RuntimeLineageError as exc:
            findings.append(str(exc))
        if not symbol_exists:
            findings.append(f"{finding_prefix}_SYMBOL_MISSING:{entry}")
    return {
        "entry": entry,
        "module": module_name,
        "symbol": symbol,
        "sourcePath": relative.as_posix(),
        "exists": exists,
        "symbolExists": symbol_exists,
    }, findings


def _discover_http(
    root: Path,
    registration: Mapping[str, Any],
) -> tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    findings: List[str] = []
    http = dict(registration.get("http") or {})
    registered_modules = _strings(http.get("registeredRouterModules") or [])
    main_path = root / str(dict(registration.get("application") or {}).get("mainFile") or "src/api/main.py")
    if not main_path.is_file():
        return [], [f"HTTP_MAIN_FILE_MISSING:{main_path}"], {}
    mounted_modules = _mounted_router_modules(main_path)
    missing_registration = sorted(set(mounted_modules) - set(registered_modules))
    stale_registration = sorted(set(registered_modules) - set(mounted_modules))
    findings.extend(f"HTTP_ROUTER_UNREGISTERED:{value}" for value in missing_registration)
    findings.extend(f"HTTP_ROUTER_NOT_MOUNTED:{value}" for value in stale_registration)

    entries = _discover_routes(
        source_path=main_path,
        module_name="src.api.main",
        decorator_owner="app",
        prefix="",
    )
    for module in registered_modules:
        path = root / "src" / "api" / "routes" / f"{module}.py"
        if not path.is_file():
            findings.append(f"HTTP_ROUTER_FILE_MISSING:{module}:{path.as_posix()}")
            continue
        entries.extend(
            _discover_routes(
                source_path=path,
                module_name=f"src.api.routes.{module}",
                decorator_owner="router",
                prefix=_router_prefix(_parse(path)),
            )
        )

    identity_seen: set[str] = set()
    duplicates: set[str] = set()
    for entry in entries:
        runtime_id = str(entry.get("runtimeId") or "")
        if runtime_id in identity_seen:
            duplicates.add(runtime_id)
        identity_seen.add(runtime_id)
    findings.extend(f"HTTP_INTERFACE_DUPLICATE:{value}" for value in sorted(duplicates))

    interface_registry = _read_object(root / INTERFACE_REGISTRY_PATH)
    discovered_pairs = {
        (str(entry.get("method") or ""), str(entry.get("path") or ""))
        for entry in entries
    }
    missing_legacy_interfaces: List[str] = []
    for definition in interface_registry.get("interfaces") or []:
        if not isinstance(definition, dict) or definition.get("status") != "ACTIVE":
            continue
        pair = (str(definition.get("method") or ""), str(definition.get("path") or ""))
        if pair not in discovered_pairs:
            interface_id = str(definition.get("interfaceId") or pair)
            missing_legacy_interfaces.append(interface_id)
            findings.append(f"REGISTERED_INTERFACE_NOT_DISCOVERED:{interface_id}:{pair[0]}:{pair[1]}")

    entries = sorted(entries, key=lambda item: str(item.get("runtimeId") or ""))
    return entries, findings, {
        "registeredRouterModules": registered_modules,
        "mountedRouterModules": mounted_modules,
        "legacyInterfaceRegistryCount": len(interface_registry.get("interfaces") or []),
        "legacyInterfacesMissingFromRuntime": missing_legacy_interfaces,
    }


def _discover_active_runners(
    root: Path,
    registration: Mapping[str, Any],
) -> tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    findings: List[str] = []
    non_http = dict(registration.get("nonHttp") or {})
    module_registry = _read_object(root / str(non_http.get("moduleRegistry") or MODULE_REGISTRY_PATH))
    projection = _read_object(root / str(non_http.get("runtimeProjection") or RUNTIME_PROJECTION_PATH))
    active_definitions = {
        str(item.get("moduleId")): dict(item)
        for item in module_registry.get("modules") or []
        if isinstance(item, dict) and item.get("status") == "ACTIVE" and item.get("moduleId")
    }
    active_ids = sorted(active_definitions)
    registered_ids = _strings(non_http.get("registeredActiveModules") or [])
    projection_modules = dict(projection.get("modules") or {})
    required_projection_ids = _strings(projection.get("requiredModules") or [])

    findings.extend(
        f"ACTIVE_MODULE_UNREGISTERED:{value}"
        for value in sorted(set(active_ids) - set(registered_ids))
    )
    findings.extend(
        f"REGISTERED_ACTIVE_MODULE_NOT_ACTIVE:{value}"
        for value in sorted(set(registered_ids) - set(active_ids))
    )
    findings.extend(
        f"ACTIVE_MODULE_MISSING_FROM_RUNTIME_PROJECTION:{value}"
        for value in sorted(set(active_ids) - set(projection_modules))
    )
    findings.extend(
        f"ACTIVE_MODULE_NOT_REQUIRED_BY_RUNTIME_PROJECTION:{value}"
        for value in sorted(set(active_ids) - set(required_projection_ids))
    )

    entries: List[Dict[str, Any]] = []
    for module_id in active_ids:
        definition = active_definitions[module_id]
        entry = str(definition.get("runner") or "")
        validation, entry_findings = _validate_python_entry(
            root=root,
            entry=entry,
            finding_prefix=f"ACTIVE_RUNNER_{module_id}",
        )
        findings.extend(entry_findings)
        projected = dict(projection_modules.get(module_id) or {})
        projected_runner = str(projected.get("runner") or "")
        if projected_runner != entry:
            findings.append(
                f"ACTIVE_RUNNER_PROJECTION_MISMATCH:{module_id}:{entry}:{projected_runner}"
            )
        entries.append(
            {
                "runtimeId": f"runner:{module_id}",
                "kind": "runner",
                "ownerModule": module_id,
                "entry": entry,
                "errorOwner": entry.partition(":")[0],
                "registryStatus": definition.get("status"),
                "runtimeProjectionRequired": module_id in required_projection_ids,
                "runtimeProjectionRunner": projected_runner,
                "validation": validation,
            }
        )
    return sorted(entries, key=lambda item: item["runtimeId"]), findings, {
        "activeRegistryModules": active_ids,
        "registeredActiveModules": registered_ids,
        "runtimeProjectionModules": sorted(projection_modules),
        "requiredRuntimeProjectionModules": required_projection_ids,
    }


def _explicit_non_http_entries(
    root: Path,
    registration: Mapping[str, Any],
) -> tuple[List[Dict[str, Any]], List[str]]:
    non_http = dict(registration.get("nonHttp") or {})
    findings: List[str] = []
    entries: List[Dict[str, Any]] = []

    for worker in non_http.get("workers") or []:
        if not isinstance(worker, dict):
            continue
        validations = {}
        for key in ("startEntry", "tickEntry", "stopEntry", "activationSource", "shutdownSource"):
            value = str(worker.get(key) or "")
            validation, entry_findings = _validate_python_entry(
                root=root,
                entry=value,
                finding_prefix=f"WORKER_{worker.get('runtimeId')}_{key}",
            )
            validations[key] = validation
            findings.extend(entry_findings)
        entries.append(
            {
                **worker,
                "kind": "worker",
                "validation": validations,
            }
        )

    for scheduler in non_http.get("schedulers") or []:
        if not isinstance(scheduler, dict):
            continue
        validations = {}
        for key in ("entry", "tickEntry"):
            value = str(scheduler.get(key) or "")
            validation, entry_findings = _validate_python_entry(
                root=root,
                entry=value,
                finding_prefix=f"SCHEDULER_{scheduler.get('runtimeId')}_{key}",
            )
            validations[key] = validation
            findings.extend(entry_findings)
        entries.append(
            {
                **scheduler,
                "kind": "scheduler",
                "validation": validations,
            }
        )

    for cli in non_http.get("cliEntries") or []:
        if not isinstance(cli, dict):
            continue
        value = str(cli.get("entry") or "")
        validation, entry_findings = _validate_python_entry(
            root=root,
            entry=value,
            finding_prefix=f"CLI_{cli.get('runtimeId')}",
        )
        findings.extend(entry_findings)
        entries.append({**cli, "kind": "cli", "validation": validation})

    return sorted(entries, key=lambda item: str(item.get("runtimeId") or "")), findings


def _server_entry(
    root: Path,
    registration: Mapping[str, Any],
) -> tuple[Dict[str, Any], List[str]]:
    application = dict(registration.get("application") or {})
    findings: List[str] = []
    required_files = [
        "mainFile",
        "startupScript",
        "deploymentScript",
        "deploymentCore",
    ]
    file_status = {}
    for key in required_files:
        relative = str(application.get(key) or "")
        exists = bool(relative and (root / relative).is_file())
        file_status[key] = {"path": relative, "exists": exists}
        if not exists:
            findings.append(f"SERVER_IDENTITY_FILE_MISSING:{key}:{relative}")

    asgi_validation, asgi_findings = _validate_python_entry(
        root=root,
        entry=str(application.get("asgiEntry") or ""),
        finding_prefix="SERVER_ASGI_ENTRY",
    )
    findings.extend(asgi_findings)

    resolver = str(application.get("serviceResolver") or "")
    resolver_validation, resolver_findings = _validate_python_entry(
        root=root,
        entry=resolver,
        finding_prefix="SERVER_SERVICE_RESOLVER",
    )
    findings.extend(resolver_findings)

    startup_path = root / str(application.get("startupScript") or "")
    deployment_core = root / str(application.get("deploymentCore") or "")
    startup_text = startup_path.read_text(encoding="utf-8") if startup_path.is_file() else ""
    deployment_text = deployment_core.read_text(encoding="utf-8") if deployment_core.is_file() else ""
    application_id = str(application.get("applicationId") or "")
    asgi_entry = str(application.get("asgiEntry") or "")
    marker_checks = {
        "startupApplicationId": f"AI_RUNTIME_APPLICATION_ID={application_id}" in startup_text
        or f'AI_RUNTIME_APPLICATION_ID="${{AI_RUNTIME_APPLICATION_ID:-{application_id}}}"' in startup_text,
        "startupAsgiEntry": asgi_entry in startup_text,
        "deploymentApplicationId": f"AI_RUNTIME_APPLICATION_ID={application_id}" in deployment_text,
        "deploymentLineageVersion": "AI_RUNTIME_LINEAGE_VERSION=Z1.0.4" in deployment_text,
    }
    for key, matched in marker_checks.items():
        if not matched:
            findings.append(f"SERVER_IDENTITY_MARKER_MISSING:{key}")

    return {
        "runtimeId": "server:ai-ecommerce-assistant",
        "kind": "server",
        "applicationId": application_id,
        "asgiEntry": asgi_entry,
        "runtimeRootEnvironment": application.get("runtimeRootEnvironment"),
        "runtimeRootDefault": application.get("runtimeRootDefault"),
        "serviceIdentityMode": application.get("serviceIdentityMode"),
        "defaultPort": application.get("defaultPort"),
        "compatibilityServiceNames": application.get("compatibilityServiceNames") or [],
        "errorOwner": "scripts.runtime_service_resolver",
        "files": file_status,
        "asgiValidation": asgi_validation,
        "resolverValidation": resolver_validation,
        "markerChecks": marker_checks,
    }, findings


def build_runtime_lineage_report(root: Path | None = None) -> Dict[str, Any]:
    repository = project_root(root)
    registration = _read_object(repository / REGISTRATION_PATH)
    if registration.get("schema") != "self_update.active_runtime_registration.v1":
        raise RuntimeLineageError("runtime_lineage_registration_schema_invalid")
    if registration.get("version") != RUNTIME_LINEAGE_VERSION:
        raise RuntimeLineageError("runtime_lineage_registration_version_invalid")

    http_entries, http_findings, http_evidence = _discover_http(repository, registration)
    runner_entries, runner_findings, runner_evidence = _discover_active_runners(
        repository, registration
    )
    explicit_entries, explicit_findings = _explicit_non_http_entries(repository, registration)
    server, server_findings = _server_entry(repository, registration)
    entries = [*http_entries, *runner_entries, *explicit_entries, server]
    findings = sorted(
        set(
            [
                *http_findings,
                *runner_findings,
                *explicit_findings,
                *server_findings,
            ]
        )
    )

    required_kinds = _strings(
        dict(registration.get("lineageAcceptance") or {}).get("requiredKinds") or []
    )
    actual_kinds = sorted({str(entry.get("kind") or "") for entry in entries})
    findings.extend(
        f"RUNTIME_KIND_MISSING:{kind}"
        for kind in sorted(set(required_kinds) - set(actual_kinds))
    )
    for entry in entries:
        if not str(entry.get("errorOwner") or "").strip():
            findings.append(f"RUNTIME_ERROR_OWNER_MISSING:{entry.get('runtimeId')}")

    findings = sorted(set(findings))
    material = {
        "version": RUNTIME_LINEAGE_VERSION,
        "applicationId": dict(registration.get("application") or {}).get("applicationId"),
        "entries": entries,
        "requiredKinds": required_kinds,
        "actualKinds": actual_kinds,
        "findings": findings,
    }
    counts = {
        kind: sum(1 for entry in entries if entry.get("kind") == kind)
        for kind in actual_kinds
    }
    return {
        "schema": "self_update.runtime_lineage_report.v1",
        **material,
        "verified": not findings,
        "counts": counts,
        "httpEvidence": http_evidence,
        "runnerEvidence": runner_evidence,
        "registrationHash": _sha256(registration),
        "lineageHash": _sha256(material),
        "repositoryWideBusinessReadExecuted": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }


def assert_runtime_lineage(root: Path | None = None) -> Dict[str, Any]:
    report = build_runtime_lineage_report(root)
    if report.get("verified") is not True:
        raise RuntimeLineageError(
            "runtime_lineage_acceptance_failed:" + ",".join(report.get("findings") or [])
        )
    return report


def runtime_lineage_summary(root: Path | None = None) -> Dict[str, Any]:
    report = build_runtime_lineage_report(root)
    return {
        "version": report.get("version"),
        "verified": report.get("verified"),
        "applicationId": report.get("applicationId"),
        "counts": report.get("counts"),
        "requiredKinds": report.get("requiredKinds"),
        "actualKinds": report.get("actualKinds"),
        "findingCount": len(report.get("findings") or []),
        "findings": report.get("findings"),
        "registrationHash": report.get("registrationHash"),
        "lineageHash": report.get("lineageHash"),
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }


def _write_report(root: Path, output: Path, report: Mapping[str, Any]) -> Path:
    target = output if output.is_absolute() else root / output
    target = target.resolve()
    if target != root and root not in target.parents:
        raise RuntimeLineageError(f"runtime_lineage_output_outside_repository:{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(report), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and verify the Z1 active runtime lineage catalog.")
    parser.add_argument("--root")
    parser.add_argument(
        "--output",
        default="outputs/runtime-lineage/runtime-lineage-report.json",
    )
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = project_root(Path(args.root) if args.root else None)
    report = build_runtime_lineage_report(root)
    target = _write_report(root, Path(args.output), report)
    print(
        json.dumps(
            {
                "verified": report.get("verified"),
                "lineageHash": report.get("lineageHash"),
                "counts": report.get("counts"),
                "findingCount": len(report.get("findings") or []),
                "output": target.relative_to(root).as_posix(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.fail_on_findings and report.get("verified") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RUNTIME_LINEAGE_VERSION",
    "RuntimeLineageError",
    "assert_runtime_lineage",
    "build_runtime_lineage_report",
    "runtime_lineage_summary",
]
