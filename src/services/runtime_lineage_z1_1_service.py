"""Z1.0.4 package-router aware runtime lineage entry.

The compiler remains stdlib-only and loads its sibling implementation by file path so
executing the lineage gate never imports ``src.__init__`` or installs the business runtime.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Mapping


def _load_base() -> ModuleType:
    path = Path(__file__).with_name("runtime_lineage_z1_service.py")
    spec = importlib.util.spec_from_file_location("_z1_runtime_lineage_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"runtime_lineage_base_load_failed:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base()


def _relative_source_path(entry: Mapping[str, Any]) -> str:
    module_name = str(entry.get("entry") or "").partition(":")[0]
    if not module_name:
        return str(entry.get("sourcePath") or "")
    return Path(*module_name.split(".")).with_suffix(".py").as_posix()


def _discover_http(
    root: Path,
    registration: Mapping[str, Any],
) -> tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    findings: List[str] = []
    http = dict(registration.get("http") or {})
    registered_modules = base._strings(http.get("registeredRouterModules") or [])
    application = dict(registration.get("application") or {})
    main_path = root / str(application.get("mainFile") or "src/api/main.py")
    if not main_path.is_file():
        return [], [f"HTTP_MAIN_FILE_MISSING:{main_path}"], {}

    mounted_modules = base._mounted_router_modules(main_path)
    findings.extend(
        f"HTTP_ROUTER_UNREGISTERED:{value}"
        for value in sorted(set(mounted_modules) - set(registered_modules))
    )
    findings.extend(
        f"HTTP_ROUTER_NOT_MOUNTED:{value}"
        for value in sorted(set(registered_modules) - set(mounted_modules))
    )

    entries = base._discover_routes(
        source_path=main_path,
        module_name="src.api.main",
        decorator_owner="app",
        prefix="",
    )
    package_router_files: Dict[str, List[str]] = {}

    for module in registered_modules:
        flat_path = root / "src" / "api" / "routes" / f"{module}.py"
        package_init = root / "src" / "api" / "routes" / module / "__init__.py"
        if flat_path.is_file():
            entries.extend(
                base._discover_routes(
                    source_path=flat_path,
                    module_name=f"src.api.routes.{module}",
                    decorator_owner="router",
                    prefix=base._router_prefix(base._parse(flat_path)),
                )
            )
            continue
        if not package_init.is_file():
            findings.append(f"HTTP_ROUTER_SOURCE_MISSING:{module}")
            continue

        package_prefix = base._router_prefix(base._parse(package_init))
        discovered_files: List[str] = []
        for source_path in sorted(package_init.parent.glob("*.py")):
            if source_path.name == "__init__.py":
                continue
            module_name = f"src.api.routes.{module}.{source_path.stem}"
            local_prefix = base._router_prefix(base._parse(source_path))
            effective_prefix = base._join_http_path(package_prefix, local_prefix)
            entries.extend(
                base._discover_routes(
                    source_path=source_path,
                    module_name=module_name,
                    decorator_owner="router",
                    prefix=effective_prefix,
                )
            )
            discovered_files.append(source_path.relative_to(root).as_posix())
        package_router_files[module] = discovered_files
        if not discovered_files:
            findings.append(f"HTTP_ROUTER_PACKAGE_EMPTY:{module}")

    for entry in entries:
        entry["sourcePath"] = _relative_source_path(entry)

    identity_seen: set[str] = set()
    duplicates: set[str] = set()
    for entry in entries:
        runtime_id = str(entry.get("runtimeId") or "")
        if runtime_id in identity_seen:
            duplicates.add(runtime_id)
        identity_seen.add(runtime_id)
    findings.extend(f"HTTP_INTERFACE_DUPLICATE:{value}" for value in sorted(duplicates))

    interface_registry = base._read_object(root / base.INTERFACE_REGISTRY_PATH)
    discovered_pairs = {
        (str(entry.get("method") or ""), str(entry.get("path") or ""))
        for entry in entries
    }
    missing_legacy_interfaces: List[str] = []
    for definition in interface_registry.get("interfaces") or []:
        if not isinstance(definition, dict) or definition.get("status") != "ACTIVE":
            continue
        pair = (
            str(definition.get("method") or ""),
            str(definition.get("path") or ""),
        )
        if pair not in discovered_pairs:
            interface_id = str(definition.get("interfaceId") or pair)
            missing_legacy_interfaces.append(interface_id)
            findings.append(
                f"REGISTERED_INTERFACE_NOT_DISCOVERED:{interface_id}:{pair[0]}:{pair[1]}"
            )

    entries = sorted(entries, key=lambda item: str(item.get("runtimeId") or ""))
    return entries, findings, {
        "registeredRouterModules": registered_modules,
        "mountedRouterModules": mounted_modules,
        "packageRouterFiles": package_router_files,
        "legacyInterfaceRegistryCount": len(interface_registry.get("interfaces") or []),
        "legacyInterfacesMissingFromRuntime": missing_legacy_interfaces,
    }


base._discover_http = _discover_http

RUNTIME_LINEAGE_VERSION = base.RUNTIME_LINEAGE_VERSION
RuntimeLineageError = base.RuntimeLineageError
build_runtime_lineage_report = base.build_runtime_lineage_report
assert_runtime_lineage = base.assert_runtime_lineage
runtime_lineage_summary = base.runtime_lineage_summary


def main(argv=None) -> int:
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RUNTIME_LINEAGE_VERSION",
    "RuntimeLineageError",
    "assert_runtime_lineage",
    "build_runtime_lineage_report",
    "runtime_lineage_summary",
]
