"""Read-only repository scanner for V23 registry drift.

The scanner compares registered module contracts with source-level evidence.  It is
purposefully conservative: findings are audit evidence, not automatic proof that a
business module is wrong.  V23.0.0-alpha.2 never blocks deployment or mutates runtime.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Set, Tuple

from .compile_registry import load_registry_documents, sha256_value
from .runner_dispatch_evidence import collect_runner_dispatch_evidence

REPOSITORY_AUDIT_VERSION = "23.0.0-alpha.3"
_DEFAULT_DISPATCH_PATHS = (
    "src/services/station_adapter_service.py",
    "src/services/station_agent_worker_v2259_service.py",
    "src/services/agent_runtime_hard_interface_v2255_service.py",
    "src/api/routes/stations.py",
    "src/api/main.py",
)
_FIELDISH = re.compile(
    r"(?:Id|ID|Hash|Ref|Version|Status|Type|Schema|Contract|Lock|Draft|Plan|Family|Reason|Code)$"
)


def _records(document: Mapping[str, Any], key: str, identity: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in document.get(key) or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get(identity) or "").strip()
        if item_id:
            result[item_id] = dict(raw)
    return result


def _camel_to_snake(value: str) -> str:
    step = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step).lower()


def _field_alias_index(fields: Mapping[str, Mapping[str, Any]]) -> Dict[str, Set[str]]:
    index: MutableMapping[str, Set[str]] = {}
    for field_id, field in fields.items():
        canonical = str(field.get("canonicalPath") or "")
        candidates = {
            canonical,
            canonical.split(".")[-1],
            field_id,
            field_id.split(".")[-1],
        }
        candidates.update(_camel_to_snake(item) for item in list(candidates) if item)
        for alias in field.get("aliases") or []:
            candidates.add(str(alias))
        for candidate in candidates:
            if candidate:
                index.setdefault(candidate, set()).add(field_id)
    return dict(index)


def _tombstone_aliases(document: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for raw in document.get("candidates") or []:
        if not isinstance(raw, dict):
            continue
        legacy_path = str(raw.get("legacyPath") or "")
        if not legacy_path:
            continue
        result[legacy_path] = dict(raw)
        result[legacy_path.split(".")[-1]] = dict(raw)
    return result


def _python_key_literals(path: Path) -> List[Tuple[str, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except Exception:
        return []
    values: List[Tuple[str, int, str]] = []
    seen: Set[Tuple[str, int, str]] = set()

    def add(value: Any, line: int, operation: str) -> None:
        if not isinstance(value, str) or not value:
            return
        item = (value, int(line or 0), operation)
        if item not in seen:
            seen.add(item)
            values.append(item)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"get", "pop", "setdefault"} and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    add(first.value, getattr(node, "lineno", 0), node.func.attr)
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant):
                operation = "write" if isinstance(node.ctx, ast.Store) else "read"
                add(key.value, getattr(node, "lineno", 0), operation)
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant):
                    add(key.value, getattr(key, "lineno", getattr(node, "lineno", 0)), "dict_key")
    return values


def _javascript_key_literals(path: Path) -> List[Tuple[str, int, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return []
    values: List[Tuple[str, int, str]] = []
    patterns = (
        re.compile(r"\[['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\]"),
        re.compile(r"\?\.([A-Za-z_][A-Za-z0-9_]*)"),
    )
    for line_number, line in enumerate(source.splitlines(), 1):
        for pattern in patterns:
            for match in pattern.finditer(line):
                values.append((match.group(1), line_number, "read"))
    return values


def _source_files(root: Path) -> List[Path]:
    paths: Set[Path] = set()
    for pattern in ("src/**/*.py", "web_demo/**/*.js"):
        paths.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def _runner_path(root: Path, runner: str) -> Tuple[Path, str, str]:
    module_name, separator, symbol = str(runner or "").partition(":")
    relative = Path(*module_name.split(".")).with_suffix(".py") if module_name else Path()
    return root / relative, symbol if separator else "", module_name


def _defined_symbols(path: Path) -> Set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return set()
    result: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    result.add(target.id)
    return result


def _dispatch_evidence(
    root: Path,
    module_name: str,
    symbol: str,
) -> List[Dict[str, Any]]:
    return collect_runner_dispatch_evidence(
        root,
        module_name,
        symbol,
        limit=20,
    )


def scan_repository(root: Path | None = None) -> Dict[str, Any]:
    repository = (root or Path(__file__).resolve().parents[2]).resolve()
    documents = load_registry_documents(repository)
    fields = _records(documents["fields.json"], "fields", "fieldId")
    modules = _records(documents["modules.json"], "modules", "moduleId")
    aliases = _field_alias_index(fields)
    tombstones = _tombstone_aliases(documents["tombstones.json"])

    file_usages: Dict[str, List[Dict[str, Any]]] = {}
    unregistered: List[Dict[str, Any]] = []
    retired_hits: List[Dict[str, Any]] = []

    for path in _source_files(repository):
        relative = path.relative_to(repository).as_posix()
        literals = _python_key_literals(path) if path.suffix == ".py" else _javascript_key_literals(path)
        usages: List[Dict[str, Any]] = []
        for key, line, operation in literals:
            matched_fields = sorted(aliases.get(key) or [])
            if matched_fields:
                usages.append(
                    {
                        "key": key,
                        "line": line,
                        "operation": operation,
                        "fieldIds": matched_fields,
                    }
                )
            if key in tombstones:
                retired_hits.append(
                    {
                        "path": relative,
                        "line": line,
                        "key": key,
                        "operation": operation,
                        "candidate": tombstones[key],
                    }
                )
            if not matched_fields and _FIELDISH.search(key) and len(key) <= 100:
                unregistered.append(
                    {
                        "path": relative,
                        "line": line,
                        "key": key,
                        "operation": operation,
                    }
                )
        if usages:
            file_usages[relative] = usages

    module_audits: Dict[str, Dict[str, Any]] = {}
    runner_drift: List[Dict[str, Any]] = []
    for module_id, module in sorted(modules.items()):
        runner = str(module.get("runner") or "")
        path, symbol, module_name = _runner_path(repository, runner)
        relative = path.relative_to(repository).as_posix() if path.is_absolute() else path.as_posix()
        file_exists = path.is_file()
        symbols = _defined_symbols(path) if file_exists else set()
        symbol_exists = bool(symbol and symbol in symbols)
        dispatch = _dispatch_evidence(repository, module_name, symbol)
        actual_fields: Set[str] = set()
        for usage in file_usages.get(relative) or []:
            actual_fields.update(str(field_id) for field_id in usage.get("fieldIds") or [])
        declared_fields = {
            str(value)
            for value in [*(module.get("reads") or []), *(module.get("writes") or [])]
            if str(value)
        }
        audit = {
            "moduleId": module_id,
            "runner": runner,
            "runnerPath": relative,
            "runnerFileExists": file_exists,
            "runnerSymbolExists": symbol_exists,
            "dispatchEvidence": dispatch,
            "dispatchEvidenceCount": len(dispatch),
            "declaredFields": sorted(declared_fields),
            "actualRegisteredFields": sorted(actual_fields),
            "actualButUndeclaredFields": sorted(actual_fields - declared_fields),
            "declaredButNotObservedInRunnerFile": sorted(declared_fields - actual_fields),
        }
        module_audits[module_id] = audit
        if not file_exists or not symbol_exists:
            runner_drift.append(
                {
                    "moduleId": module_id,
                    "runner": runner,
                    "runnerFileExists": file_exists,
                    "runnerSymbolExists": symbol_exists,
                    "classification": "declared_runner_unresolvable",
                }
            )
        elif not dispatch and str(module.get("status") or "") == "ACTIVE":
            runner_drift.append(
                {
                    "moduleId": module_id,
                    "runner": runner,
                    "runnerFileExists": True,
                    "runnerSymbolExists": True,
                    "classification": "live_dispatch_evidence_not_found",
                }
            )

    unregistered_sorted = sorted(
        unregistered,
        key=lambda item: (item["path"], int(item["line"]), item["key"]),
    )[:500]
    retired_sorted = sorted(
        retired_hits,
        key=lambda item: (item["path"], int(item["line"]), item["key"]),
    )[:500]
    material = {
        "moduleAudits": module_audits,
        "runnerDrift": runner_drift,
        "unregisteredFieldCandidates": unregistered_sorted,
        "retiredFieldCandidateHits": retired_sorted,
    }
    return {
        "schema": "registry.repository_audit.v1",
        "version": REPOSITORY_AUDIT_VERSION,
        "mode": "report_only",
        "repositoryScanHash": sha256_value(material),
        "summary": {
            "sourceFileCount": len(_source_files(repository)),
            "filesWithRegisteredFieldUsage": len(file_usages),
            "registeredModuleCount": len(modules),
            "runnerDriftCount": len(runner_drift),
            "unregisteredFieldCandidateCount": len(unregistered_sorted),
            "retiredFieldCandidateHitCount": len(retired_sorted),
        },
        **material,
        "deploymentBlocked": False,
        "businessRuntimeMutated": False,
    }
