#!/usr/bin/env python3
"""Compile the public competition runtime registry and hash-lineage graph.

The compiler starts from the declared production entrypoint and the existing
V23 runtime registry projection. It follows actual local Python imports and
frontend references, verifies every registry implementation/runner, rejects
private-core boundary crossings, and writes deterministic evidence files.

It does not guess by filename similarity and it never copies files.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "competition.lineage.compiler.v1"
HTML_REF_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"'?#]+)""", re.IGNORECASE)
JS_IMPORT_RE = re.compile(
    r"""(?:from\s+|import\s*\(\s*|require\s*\(\s*)["']([^"']+)["']"""
)
CSS_REF_RE = re.compile(
    r"""(?:url\(\s*|@import\s+)["']?([^"')\s?#]+)""",
    re.IGNORECASE,
)
FASTAPI_ASSIGNMENT_NAMES = {"FastAPI"}


class CompetitionLineageError(RuntimeError):
    """Raised when the public runtime cannot be proven."""


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


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CompetitionLineageError(f"JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise CompetitionLineageError(f"JSON_OBJECT_REQUIRED:{path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def safe_relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise CompetitionLineageError(f"PATH_ESCAPES_ROOT:{path}") from exc


def git_commit(root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    for name in ("GITHUB_SHA", "SOURCE_COMMIT"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def module_to_path(root: Path, module_name: str) -> Path | None:
    clean = module_name.strip(".")
    if not clean:
        return None
    candidate = root.joinpath(*clean.split(".")).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = root.joinpath(*clean.split("."), "__init__.py")
    if package.is_file():
        return package
    return None


def path_to_module(root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = Path(parts[-1]).stem
    return ".".join(parts)


def _relative_base(current_module: str, current_path: Path, level: int) -> list[str]:
    parts = current_module.split(".")
    package_parts = parts if current_path.name == "__init__.py" else parts[:-1]
    if level <= 0:
        return package_parts
    remove_count = level - 1
    if remove_count > len(package_parts):
        return []
    return package_parts[: len(package_parts) - remove_count]


def python_import_modules(root: Path, path: Path) -> tuple[list[str], list[str]]:
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return [], [f"PYTHON_SYNTAX_ERROR:{safe_relative(root, path)}:{exc.lineno}:{exc.msg}"]

    current_module = path_to_module(root, path)
    modules: set[str] = set()
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = _relative_base(current_module, path, node.level)
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(part for part in base_parts if part)
            else:
                base = node.module or ""
            if base:
                modules.add(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                child = ".".join(part for part in (base, alias.name) if part)
                if child:
                    modules.add(child)

    return sorted(modules), findings


def top_level_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            else:
                targets.append(node.target)
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
    return symbols


def add_package_initializers(root: Path, paths: set[str]) -> None:
    additions: set[str] = set()
    for relative in list(paths):
        path = root / relative
        if path.suffix != ".py":
            continue
        parent = path.parent
        while parent != root and root in parent.parents:
            initializer = parent / "__init__.py"
            if initializer.is_file():
                additions.add(safe_relative(root, initializer))
            parent = parent.parent
    paths.update(additions)


def normalize_local_reference(root: Path, source: Path, raw: str) -> Path | None:
    reference = raw.strip()
    if not reference or reference.startswith(("http://", "https://", "//", "data:", "mailto:", "#")):
        return None

    if reference.startswith("/web_demo/"):
        candidate = root / reference.lstrip("/")
    elif reference.startswith("/"):
        return None
    else:
        candidate = source.parent / reference

    candidate = candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None

    if candidate.is_dir():
        index = candidate / "index.html"
        return index if index.is_file() else None
    if candidate.is_file():
        return candidate

    if candidate.suffix == "":
        for suffix in (".js", ".css", ".html"):
            expanded = candidate.with_suffix(suffix)
            if expanded.is_file():
                return expanded
        index = candidate / "index.js"
        if index.is_file():
            return index
    return None


def frontend_references(root: Path, path: Path) -> list[Path]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    raw_refs: list[str] = []
    if path.suffix.lower() in {".html", ".htm"}:
        raw_refs.extend(HTML_REF_RE.findall(text))
    elif path.suffix.lower() in {".js", ".mjs"}:
        raw_refs.extend(JS_IMPORT_RE.findall(text))
    elif path.suffix.lower() == ".css":
        raw_refs.extend(CSS_REF_RE.findall(text))

    result: list[Path] = []
    for raw in raw_refs:
        resolved = normalize_local_reference(root, path, raw)
        if resolved is not None:
            result.append(resolved)
    return sorted(set(result))


def is_allowed(path: str, prefixes: Sequence[str]) -> bool:
    for prefix in prefixes:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return True
        elif path == prefix:
            return True
    return False


def has_forbidden_prefix(path: str, prefixes: Sequence[str]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


def detect_fastapi_entrypoints(root: Path, search_prefixes: Sequence[str]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for prefix in search_prefixes:
        base = root / prefix
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in candidates:
            if not path.is_file() or path.suffix != ".py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                if not isinstance(value, ast.Call):
                    continue
                func = value.func
                func_name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else ""
                )
                if func_name not in FASTAPI_ASSIGNMENT_NAMES:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        entries.append(
                            {
                                "entrypoint": f"{path_to_module(root, path)}:{target.id}",
                                "path": safe_relative(root, path),
                            }
                        )
    return sorted(entries, key=lambda item: (item["entrypoint"], item["path"]))


def compile_lineage(
    root: Path,
    *,
    scope: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    source_commit: str,
) -> dict[str, Any]:
    findings: list[str] = []
    warnings: list[str] = []
    roles: dict[str, set[str]] = defaultdict(set)
    registry_owners: dict[str, set[str]] = defaultdict(set)
    edges: set[tuple[str, str, str]] = set()
    runtime_paths: set[str] = set()

    allowed_prefixes = [str(item) for item in scope.get("allowedPrefixes") or []]
    forbidden_prefixes = [str(item) for item in scope.get("forbiddenPrefixes") or []]
    forbidden_names = {str(item) for item in scope.get("forbiddenFileNames") or []}

    registry_path = root / str(scope.get("registryProjectionPath"))
    if not registry_path.is_file():
        raise CompetitionLineageError(f"REGISTRY_PROJECTION_MISSING:{registry_path}")
    registry = read_object(registry_path)
    expected_registry_hash = str(source_identity.get("registryRootHash") or "")
    actual_registry_hash = str(registry.get("registryRootHash") or "")
    if expected_registry_hash and actual_registry_hash != expected_registry_hash:
        findings.append(
            f"REGISTRY_ROOT_HASH_MISMATCH:{actual_registry_hash}:{expected_registry_hash}"
        )

    declared_modules = registry.get("modules")
    if not isinstance(declared_modules, dict):
        raise CompetitionLineageError("REGISTRY_MODULES_OBJECT_REQUIRED")

    required_modules = [
        str(item)
        for item in (
            scope.get("requiredRegistryModules")
            or registry.get("requiredModules")
            or []
        )
    ]
    for module_id in required_modules:
        if module_id not in declared_modules:
            findings.append(f"REQUIRED_REGISTRY_MODULE_MISSING:{module_id}")

    production_entrypoints = [str(item) for item in scope.get("productionEntrypoints") or []]
    if len(production_entrypoints) != 1:
        findings.append(
            f"EXACTLY_ONE_PRODUCTION_ENTRYPOINT_REQUIRED:{len(production_entrypoints)}"
        )

    python_queue: deque[Path] = deque()
    frontend_queue: deque[Path] = deque()

    def add_runtime_file(path: Path, role: str) -> str | None:
        if not path.is_file() or path.is_symlink():
            findings.append(f"RUNTIME_FILE_MISSING_OR_UNSAFE:{safe_relative(root, path)}")
            return None
        relative = safe_relative(root, path)
        runtime_paths.add(relative)
        roles[relative].add(role)
        return relative

    for entrypoint in production_entrypoints:
        module_name, separator, symbol = entrypoint.partition(":")
        if not separator or not module_name or not symbol:
            findings.append(f"INVALID_PRODUCTION_ENTRYPOINT:{entrypoint}")
            continue
        path = module_to_path(root, module_name)
        if path is None:
            findings.append(f"PRODUCTION_ENTRYPOINT_MODULE_MISSING:{entrypoint}")
            continue
        relative = add_runtime_file(path, "production_entrypoint")
        if relative:
            python_queue.append(path)
            try:
                if symbol not in top_level_symbols(path):
                    findings.append(f"PRODUCTION_ENTRYPOINT_SYMBOL_MISSING:{entrypoint}")
            except SyntaxError as exc:
                findings.append(f"PRODUCTION_ENTRYPOINT_SYNTAX_ERROR:{entrypoint}:{exc}")

    for raw in scope.get("seedFiles") or []:
        path = root / str(raw)
        relative = add_runtime_file(path, "fixed_runtime_seed")
        if relative and path.suffix == ".py":
            python_queue.append(path)
        elif relative and path.suffix.lower() in {".html", ".js", ".mjs", ".css"}:
            frontend_queue.append(path)

    for pattern in scope.get("seedGlobs") or []:
        matches = sorted(path for path in root.glob(str(pattern)) if path.is_file())
        if not matches:
            findings.append(f"SEED_GLOB_EMPTY:{pattern}")
        for path in matches:
            relative = add_runtime_file(path, f"fixed_runtime_glob:{pattern}")
            if relative and path.suffix == ".py":
                python_queue.append(path)
            elif relative and path.suffix.lower() in {".html", ".js", ".mjs", ".css"}:
                frontend_queue.append(path)

    for raw in scope.get("staticRoots") or []:
        path = root / str(raw)
        relative = add_runtime_file(path, "frontend_root")
        if relative:
            frontend_queue.append(path)

    registry_snapshot_modules: dict[str, Any] = {}
    for module_id in required_modules:
        raw_module = declared_modules.get(module_id)
        if not isinstance(raw_module, dict):
            continue
        implementation_paths = sorted(
            {str(item) for item in raw_module.get("implementationPaths") or []}
        )
        module_record: dict[str, Any] = {
            "moduleId": module_id,
            "fieldIds": sorted({str(item) for item in raw_module.get("fieldIds") or []}),
            "schemaIds": sorted({str(item) for item in raw_module.get("schemaIds") or []}),
            "implementationPaths": implementation_paths,
            "runner": raw_module.get("runner"),
        }
        registry_node = f"registry:{module_id}"

        for relative in implementation_paths:
            path = root / relative
            resolved = add_runtime_file(path, "registry_implementation")
            if not resolved:
                findings.append(f"REGISTRY_IMPLEMENTATION_MISSING:{module_id}:{relative}")
                continue
            registry_owners[resolved].add(module_id)
            edges.add((registry_node, f"file:{resolved}", "REGISTRY_OWNS"))
            if path.suffix == ".py":
                python_queue.append(path)
            elif path.suffix.lower() in {".html", ".js", ".mjs", ".css"}:
                frontend_queue.append(path)

        runner = str(raw_module.get("runner") or "")
        if runner:
            runner_module, separator, runner_symbol = runner.partition(":")
            if not separator or not runner_module or not runner_symbol:
                findings.append(f"INVALID_REGISTRY_RUNNER:{module_id}:{runner}")
            else:
                runner_path = module_to_path(root, runner_module)
                if runner_path is None:
                    findings.append(f"REGISTRY_RUNNER_MODULE_MISSING:{module_id}:{runner}")
                else:
                    resolved = add_runtime_file(runner_path, "registry_runner")
                    if resolved:
                        python_queue.append(runner_path)
                        registry_owners[resolved].add(module_id)
                        edges.add((registry_node, f"file:{resolved}", "RUNS"))
                        module_record["runnerPath"] = resolved
                        try:
                            if runner_symbol not in top_level_symbols(runner_path):
                                findings.append(
                                    f"REGISTRY_RUNNER_SYMBOL_MISSING:{module_id}:{runner}"
                                )
                        except SyntaxError as exc:
                            findings.append(
                                f"REGISTRY_RUNNER_SYNTAX_ERROR:{module_id}:{runner}:{exc}"
                            )
        else:
            findings.append(f"REGISTRY_RUNNER_MISSING:{module_id}")

        registry_snapshot_modules[module_id] = module_record

    visited_python: set[str] = set()
    while python_queue:
        path = python_queue.popleft()
        relative = safe_relative(root, path)
        if relative in visited_python:
            continue
        visited_python.add(relative)
        add_runtime_file(path, "python_import_closure")
        imported_modules, import_findings = python_import_modules(root, path)
        findings.extend(import_findings)
        for imported_module in imported_modules:
            imported_path = module_to_path(root, imported_module)
            if imported_path is None:
                continue
            imported_relative = add_runtime_file(imported_path, "python_import_closure")
            if not imported_relative:
                continue
            edges.add((f"file:{relative}", f"file:{imported_relative}", "IMPORTS"))
            python_queue.append(imported_path)

    visited_frontend: set[str] = set()
    while frontend_queue:
        path = frontend_queue.popleft()
        relative = safe_relative(root, path)
        if relative in visited_frontend:
            continue
        visited_frontend.add(relative)
        add_runtime_file(path, "frontend_reference_closure")
        for referenced in frontend_references(root, path):
            referenced_relative = add_runtime_file(referenced, "frontend_reference_closure")
            if not referenced_relative:
                continue
            edges.add((f"file:{relative}", f"file:{referenced_relative}", "REFERENCES"))
            frontend_queue.append(referenced)

    add_package_initializers(root, runtime_paths)

    for relative in sorted(runtime_paths):
        if forbidden_names and Path(relative).name in forbidden_names:
            findings.append(f"FORBIDDEN_FILE_NAME_IN_RUNTIME:{relative}")
        if has_forbidden_prefix(relative, forbidden_prefixes):
            findings.append(f"PRIVATE_BOUNDARY_CROSSING:{relative}")
        if allowed_prefixes and not is_allowed(relative, allowed_prefixes):
            findings.append(f"RUNTIME_PATH_OUTSIDE_ALLOWLIST_PREFIXES:{relative}")

    candidate_entrypoints = detect_fastapi_entrypoints(
        root,
        [str(item) for item in scope.get("entrypointSearchPrefixes") or ["src/"]],
    )
    declared_set = set(production_entrypoints)
    shadow_entrypoints = [
        item for item in candidate_entrypoints if item["entrypoint"] not in declared_set
    ]
    if shadow_entrypoints:
        message = "SHADOW_FASTAPI_ENTRYPOINTS:" + ",".join(
            item["entrypoint"] for item in shadow_entrypoints
        )
        if bool(scope.get("failOnShadowEntrypoints", True)):
            findings.append(message)
        else:
            warnings.append(message)

    file_records: list[dict[str, Any]] = []
    for relative in sorted(runtime_paths):
        path = root / relative
        if not path.is_file():
            continue
        file_records.append(
            {
                "path": relative,
                "sha256": file_hash(path),
                "size": path.stat().st_size,
                "roles": sorted(roles.get(relative) or []),
                "registryModules": sorted(registry_owners.get(relative) or []),
            }
        )

    runtime_material = [
        {
            "path": item["path"],
            "sha256": item["sha256"],
            "size": item["size"],
        }
        for item in file_records
    ]
    runtime_hash = canonical_hash(runtime_material)

    file_nodes = [
        {
            "id": f"file:{item['path']}",
            "type": "file",
            "path": item["path"],
            "sha256": item["sha256"],
            "roles": item["roles"],
            "registryModules": item["registryModules"],
        }
        for item in file_records
    ]
    registry_nodes = [
        {
            "id": f"registry:{module_id}",
            "type": "registry_module",
            "moduleId": module_id,
        }
        for module_id in sorted(registry_snapshot_modules)
    ]
    edge_records = [
        {"from": source, "to": target, "type": edge_type}
        for source, target, edge_type in sorted(edges)
    ]
    graph_material = {
        "nodes": sorted(file_nodes + registry_nodes, key=lambda item: item["id"]),
        "edges": edge_records,
    }
    graph_hash = canonical_hash(graph_material)

    source_identity_hash = canonical_hash(source_identity)
    registry_snapshot_material = {
        "schema": "competition.registry_snapshot.v1",
        "compilerSchema": SCHEMA_VERSION,
        "sourceCommit": source_commit,
        "sourceIdentityHash": source_identity_hash,
        "registryProjectionPath": safe_relative(root, registry_path),
        "registryRootHash": actual_registry_hash,
        "registryVersion": registry.get("version"),
        "registryReleaseVersion": registry.get("releaseVersion"),
        "productionEntrypoints": production_entrypoints,
        "requiredModules": required_modules,
        "modules": registry_snapshot_modules,
        "runtimeFileCount": len(file_records),
        "runtimeHash": runtime_hash,
    }
    registry_snapshot = {
        **registry_snapshot_material,
        "snapshotHash": canonical_hash(registry_snapshot_material),
    }

    lineage_graph_material = {
        "schema": "competition.hash_lineage_graph.v1",
        "compilerSchema": SCHEMA_VERSION,
        "sourceCommit": source_commit,
        "sourceIdentityHash": source_identity_hash,
        "registryRootHash": actual_registry_hash,
        **graph_material,
    }
    lineage_graph = {
        **lineage_graph_material,
        "graphHash": graph_hash,
    }

    verification_material = {
        "schema": "competition.lineage_verification.v1",
        "sourceCommit": source_commit,
        "sourceIdentityHash": source_identity_hash,
        "registryRootHash": actual_registry_hash,
        "runtimeHash": runtime_hash,
        "graphHash": graph_hash,
        "runtimeFileCount": len(file_records),
        "registryModuleCount": len(registry_snapshot_modules),
        "candidateEntrypoints": candidate_entrypoints,
        "shadowEntrypoints": shadow_entrypoints,
        "findings": sorted(set(findings)),
        "warnings": sorted(set(warnings)),
    }
    verification_report = {
        **verification_material,
        "verified": not findings,
        "verificationHash": canonical_hash(verification_material),
    }

    manifest_material = {
        "schema": "competition.lineage_evidence_manifest.v1",
        "sourceCommit": source_commit,
        "sourceIdentityHash": source_identity_hash,
        "registrySnapshotHash": registry_snapshot["snapshotHash"],
        "runtimeHash": runtime_hash,
        "lineageGraphHash": graph_hash,
        "verificationHash": verification_report["verificationHash"],
        "verified": verification_report["verified"],
        "entrypoint": production_entrypoints[0] if len(production_entrypoints) == 1 else None,
    }
    evidence_manifest = {
        **manifest_material,
        "manifestHash": canonical_hash(manifest_material),
    }

    return {
        "sourceIdentity": dict(source_identity),
        "registrySnapshot": registry_snapshot,
        "lineageGraph": lineage_graph,
        "runtimeFiles": file_records,
        "verificationReport": verification_report,
        "evidenceManifest": evidence_manifest,
    }


def write_outputs(output_dir: Path, compiled: Mapping[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "source-identity.json", compiled["sourceIdentity"])
    write_json(output_dir / "registry-snapshot.json", compiled["registrySnapshot"])
    write_json(output_dir / "lineage-graph.json", compiled["lineageGraph"])
    write_json(output_dir / "verification-report.json", compiled["verificationReport"])
    write_json(output_dir / "evidence-manifest.json", compiled["evidenceManifest"])

    file_records = list(compiled["runtimeFiles"])
    (output_dir / "runtime-files.txt").write_text(
        "".join(f"{item['path']}\n" for item in file_records),
        encoding="utf-8",
    )
    (output_dir / "runtime-files.sha256").write_text(
        "".join(
            f"{str(item['sha256']).removeprefix('sha256:')}  {item['path']}\n"
            for item in file_records
        ),
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile the public competition registry and hash-lineage evidence."
    )
    parser.add_argument("--root", default=None)
    parser.add_argument(
        "--scope",
        default="config/competition_runtime_scope.json",
    )
    parser.add_argument(
        "--source-identity",
        default="config/competition_source_identity.json",
    )
    parser.add_argument(
        "--output-dir",
        default="dist/competition-lineage",
    )
    parser.add_argument("--source-commit", default=None)
    parser.add_argument(
        "--allow-findings",
        action="store_true",
        help="Write evidence but do not return a non-zero status when findings exist.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    scope_path = root / args.scope
    source_identity_path = root / args.source_identity
    output_dir = root / args.output_dir

    scope = read_object(scope_path)
    source_identity = read_object(source_identity_path)
    compiled = compile_lineage(
        root,
        scope=scope,
        source_identity=source_identity,
        source_commit=git_commit(root, args.source_commit),
    )
    write_outputs(output_dir, compiled)

    report = dict(compiled["verificationReport"])
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "runtimeFileCount": report["runtimeFileCount"],
                "registryModuleCount": report["registryModuleCount"],
                "runtimeHash": report["runtimeHash"],
                "graphHash": report["graphHash"],
                "findings": report["findings"],
                "warnings": report["warnings"],
                "outputDirectory": safe_relative(root, output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if report["verified"] or args.allow_findings:
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"competition lineage compilation failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
