"""Deterministic AST evidence for registered Runner dispatch.

The scanner follows exact Python module identities, public compatibility re-exports,
and registered-symbol aliases. It returns call evidence only; imports by themselves do
not prove that a Runner participates in an active chain. Historical V22.5.5 and explicit
legacy paths are excluded from positive dispatch evidence.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Set, Tuple


RUNNER_DISPATCH_EVIDENCE_VERSION = "23.0.0-alpha.3"
_LEGACY_PATH_MARKERS = ("_legacy", "v2255")


def _module_path(root: Path, module_name: str) -> Path:
    return root / Path(*str(module_name or "").split(".")).with_suffix(".py")


def _module_name(root: Path, path: Path) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


@lru_cache(maxsize=None)
def _source_text(path_value: str) -> str:
    try:
        return Path(path_value).read_text(encoding="utf-8")
    except Exception:
        return ""


@lru_cache(maxsize=None)
def _parse_path(path_value: str) -> Optional[ast.Module]:
    source = _source_text(path_value)
    if not source:
        return None
    try:
        return ast.parse(source, filename=path_value)
    except Exception:
        return None


def _parse(path: Path) -> Optional[ast.Module]:
    return _parse_path(str(path.resolve()))


def _source_line(path: Path, line_number: int) -> str:
    lines = _source_text(str(path.resolve())).splitlines()
    index = int(line_number or 0) - 1
    if index < 0 or index >= len(lines):
        return ""
    return " ".join(lines[index].strip().split())[:220]


@lru_cache(maxsize=None)
def _source_files(root_value: str) -> Tuple[Path, ...]:
    root = Path(root_value)
    return tuple(
        sorted(
            (path.resolve() for path in root.glob("src/**/*.py") if path.is_file()),
            key=lambda item: item.relative_to(root).as_posix(),
        )
    )


@lru_cache(maxsize=None)
def _top_level_import_index(
    root_value: str,
) -> Tuple[Tuple[str, str, Tuple[str, ...]], ...]:
    root = Path(root_value)
    records: List[Tuple[str, str, Tuple[str, ...]]] = []
    for path in _source_files(root_value):
        tree = _parse(path)
        if tree is None:
            continue
        candidate_module = _module_name(root, path)
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            provider = str(node.module or "")
            imported = tuple(sorted(str(alias.name) for alias in node.names))
            if provider and imported:
                records.append((candidate_module, provider, imported))
    return tuple(records)


def _registered_symbol_aliases(
    root: Path,
    module_name: str,
    registered_symbol: str,
) -> Set[str]:
    """Resolve same-module public aliases such as public_name = implementation_name."""
    symbols = {str(registered_symbol)} if str(registered_symbol) else set()
    tree = _parse(_module_path(root, module_name))
    if tree is None:
        return symbols

    changed = True
    while changed:
        changed = False
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not isinstance(value, ast.Name):
                continue
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id in symbols
                    and value.id not in symbols
                ):
                    symbols.add(value.id)
                    changed = True
    return symbols


def _provider_modules(
    root: Path,
    module_name: str,
    symbols: Set[str],
) -> Set[str]:
    """Follow top-level public imports that preserve the registered callable identity."""
    providers = {str(module_name)}
    records = _top_level_import_index(str(root.resolve()))
    changed = True
    while changed:
        changed = False
        for candidate_module, provider, imported in records:
            if candidate_module in providers or provider not in providers:
                continue
            imported_set = set(imported)
            if "*" in imported_set or bool(imported_set & symbols):
                providers.add(candidate_module)
                changed = True
    return providers


def _is_legacy_path(relative_path: str) -> bool:
    lowered = str(relative_path).lower()
    return any(marker in lowered for marker in _LEGACY_PATH_MARKERS)


def collect_runner_dispatch_evidence(
    root: Path,
    module_name: str,
    registered_symbol: str,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Return exact non-legacy call evidence for one registered Runner."""
    repository = root.resolve()
    source_files = _source_files(str(repository))
    symbols = _registered_symbol_aliases(
        repository,
        module_name,
        registered_symbol,
    )
    providers = _provider_modules(repository, module_name, symbols)

    evidence: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, int, str, str]] = set()

    for path in source_files:
        relative = path.relative_to(repository).as_posix()
        if _is_legacy_path(relative):
            continue
        tree = _parse(path)
        if tree is None:
            continue

        imported_callables: MutableMapping[str, Tuple[str, str]] = {}
        imported_modules: MutableMapping[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                provider = str(node.module or "")
                if provider not in providers:
                    continue
                for alias in node.names:
                    source_symbol = str(alias.name)
                    if source_symbol not in symbols:
                        continue
                    local_name = str(alias.asname or source_symbol)
                    imported_callables[local_name] = (provider, source_symbol)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    provider = str(alias.name)
                    if provider not in providers:
                        continue
                    local_name = str(alias.asname or provider.split(".")[-1])
                    imported_modules[local_name] = provider

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            kind = ""
            matched_symbol = ""
            provider_module = ""

            if isinstance(function, ast.Name) and function.id in imported_callables:
                provider_module, matched_symbol = imported_callables[function.id]
                kind = "imported_callable_call"
            elif (
                isinstance(function, ast.Attribute)
                and function.attr in symbols
                and isinstance(function.value, ast.Name)
                and function.value.id in imported_modules
            ):
                provider_module = imported_modules[function.value.id]
                matched_symbol = function.attr
                kind = "module_attribute_call"

            if not kind:
                continue

            line_number = int(getattr(node, "lineno", 0) or 0)
            identity = (relative, line_number, kind, matched_symbol)
            if identity in seen:
                continue
            seen.add(identity)
            evidence.append(
                {
                    "path": relative,
                    "line": line_number,
                    "kind": kind,
                    "registeredModule": str(module_name),
                    "providerModule": provider_module,
                    "registeredSymbol": str(registered_symbol),
                    "matchedSymbol": matched_symbol,
                    "symbolAliases": sorted(symbols),
                    "providerDepthResolved": provider_module != str(module_name),
                    "legacyPath": False,
                    "text": _source_line(path, line_number),
                }
            )

    return sorted(
        evidence,
        key=lambda item: (
            str(item.get("path") or ""),
            int(item.get("line") or 0),
            str(item.get("kind") or ""),
            str(item.get("matchedSymbol") or ""),
        ),
    )[: max(1, int(limit))]


__all__ = [
    "RUNNER_DISPATCH_EVIDENCE_VERSION",
    "collect_runner_dispatch_evidence",
]
