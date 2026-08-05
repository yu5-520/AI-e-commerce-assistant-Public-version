"""Classify unregistered field candidates through the selected module path closure."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


SCHEMA = "contest.unregistered_field_scope_review.v1"
_CLASSIFICATION_ORDER = (
    "CORE_REVIEW",
    "SUPPORT_REVIEW",
    "ISOLATE_DEFER",
    "OUTSIDE_CONTEST_SCOPE",
)
_JS_IMPORT = re.compile(
    r"(?:from\s+|import\s*\(|import\s+)[\"']([^\"']+)[\"']"
)


def _read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _normal_path(path: Path, root: Path) -> Optional[str]:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return None


def _python_module_path(root: Path, module_name: str) -> Optional[Path]:
    if not module_name:
        return None
    candidate = root / Path(*module_name.split("."))
    py_file = candidate.with_suffix(".py")
    if py_file.is_file():
        return py_file
    init_file = candidate / "__init__.py"
    return init_file if init_file.is_file() else None


def _relative_module_name(path: Path, level: int, module: str) -> str:
    current_parts = list(path.with_suffix("").parts[:-1])
    if path.name == "__init__.py":
        current_parts = list(path.parts[:-1])
    trim = max(0, int(level or 0) - 1)
    if trim:
        current_parts = current_parts[:-trim]
    module_parts = [item for item in str(module or "").split(".") if item]
    return ".".join([*current_parts, *module_parts])


def _python_dependencies(root: Path, relative: str) -> Set[str]:
    path = root / relative
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return set()
    result: Set[str] = set()
    relative_path = path.relative_to(root)
    for node in ast.walk(tree):
        module_name = ""
        if isinstance(node, ast.Import):
            for alias in node.names:
                dependency = _python_module_path(root, str(alias.name))
                normalized = _normal_path(dependency, root) if dependency else None
                if normalized:
                    result.add(normalized)
        elif isinstance(node, ast.ImportFrom):
            if int(node.level or 0) > 0:
                module_name = _relative_module_name(
                    relative_path,
                    int(node.level or 0),
                    str(node.module or ""),
                )
            else:
                module_name = str(node.module or "")
            dependency = _python_module_path(root, module_name)
            normalized = _normal_path(dependency, root) if dependency else None
            if normalized:
                result.add(normalized)
    return result


def _javascript_dependencies(root: Path, relative: str) -> Set[str]:
    path = root / relative
    try:
        source = path.read_text(encoding="utf-8")
    except Exception:
        return set()
    result: Set[str] = set()
    for match in _JS_IMPORT.finditer(source):
        target = str(match.group(1) or "")
        if not target.startswith("."):
            continue
        candidate = (path.parent / target).resolve()
        candidates = [candidate]
        if not candidate.suffix:
            candidates.extend(
                [candidate.with_suffix(".js"), candidate / "index.js"]
            )
        for dependency in candidates:
            if not dependency.is_file():
                continue
            normalized = _normal_path(dependency, root)
            if normalized:
                result.add(normalized)
                break
    return result


def _dependencies(root: Path, relative: str) -> Set[str]:
    if relative.endswith(".py"):
        return _python_dependencies(root, relative)
    if relative.endswith(".js"):
        return _javascript_dependencies(root, relative)
    return set()


def _closure(root: Path, seeds: Iterable[str]) -> Tuple[Set[str], Dict[str, str]]:
    visited: Set[str] = set()
    origin: Dict[str, str] = {}
    queue = deque()
    for value in sorted(set(str(item) for item in seeds if str(item))):
        if not (root / value).is_file():
            continue
        queue.append(value)
        origin[value] = "REGISTERED_PHYSICAL_PATH"
    while queue:
        relative = queue.popleft()
        if relative in visited:
            continue
        visited.add(relative)
        for dependency in sorted(_dependencies(root, relative)):
            if dependency in visited:
                continue
            origin.setdefault(dependency, f"IMPORT_CLOSURE:{relative}")
            queue.append(dependency)
    return visited, origin


def _registered_seeds(selection: Mapping[str, Any]) -> Dict[str, Set[str]]:
    result: Dict[str, Set[str]] = {
        "KEEP_CORE": set(),
        "KEEP_SUPPORT": set(),
        "ISOLATE": set(),
    }
    for raw in selection.get("physicalPathReview") or []:
        if not isinstance(raw, dict):
            continue
        classification = str(raw.get("classification") or "")
        path = str(raw.get("path") or "")
        if classification in result and path:
            result[classification].add(path)
    return result


def _candidate_classification(scopes: Set[str]) -> str:
    if "KEEP_CORE" in scopes:
        return "CORE_REVIEW"
    if "KEEP_SUPPORT" in scopes:
        return "SUPPORT_REVIEW"
    if "ISOLATE" in scopes:
        return "ISOLATE_DEFER"
    return "OUTSIDE_CONTEST_SCOPE"


def _render_markdown(report: Mapping[str, Any]) -> str:
    counts = dict(report.get("summary", {}).get("classificationCounts") or {})
    lines = [
        "# Unregistered Field Scope Review",
        "",
        f"- Scope hash: `{report.get('scopeHash')}`",
        f"- Source candidate count: {report.get('summary', {}).get('sourceCandidateCount', 0)}",
        f"- Registered path seed count: {report.get('summary', {}).get('registeredPathSeedCount', 0)}",
        f"- Import-closure path count: {report.get('summary', {}).get('closurePathCount', 0)}",
        "",
        "## Classification",
        "",
    ]
    for classification in _CLASSIFICATION_ORDER:
        lines.append(f"- {classification}: {counts.get(classification, 0)}")
    lines.extend(
        [
            "",
            "## Review rule",
            "",
            "Only `CORE_REVIEW` and `SUPPORT_REVIEW` candidates belong to the current",
            "contest-chain governance review. `ISOLATE_DEFER` remains attached to isolated",
            "registered modules. `OUTSIDE_CONTEST_SCOPE` is not evidence for changing the",
            "contest chain and must not block contest pruning.",
            "",
            "## Paths with review candidates",
            "",
            "| Classification | Path | Candidate count |",
            "|---|---|---:|",
        ]
    )
    path_counts = report.get("pathCounts") or []
    for item in path_counts:
        if item.get("classification") == "OUTSIDE_CONTEST_SCOPE":
            continue
        lines.append(
            f"| {item.get('classification')} | {item.get('path')} | {item.get('count')} |"
        )
    lines.append("")
    return "\n".join(lines)


def build_review(root: Path, generated_dir: Path) -> Dict[str, Any]:
    selection = _read_object(generated_dir / "contest-selection-manifest.json")
    audit = _read_object(generated_dir / "repository-audit.json")
    seeds = _registered_seeds(selection)

    closures: Dict[str, Set[str]] = {}
    origins: Dict[str, Dict[str, str]] = {}
    for classification in ("KEEP_CORE", "KEEP_SUPPORT", "ISOLATE"):
        closure, origin = _closure(root, seeds[classification])
        closures[classification] = closure
        origins[classification] = origin

    candidates: List[Dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    path_counts: MutableMapping[Tuple[str, str], int] = defaultdict(int)
    for raw in audit.get("unregisteredFieldCandidates") or []:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "")
        scopes = {
            classification
            for classification, paths in closures.items()
            if path in paths
        }
        classification = _candidate_classification(scopes)
        classification_counts[classification] += 1
        path_counts[(classification, path)] += 1
        candidates.append(
            {
                **raw,
                "classification": classification,
                "registeredScopes": sorted(scopes),
                "scopeOrigins": {
                    scope: origins.get(scope, {}).get(path)
                    for scope in sorted(scopes)
                },
                "contestReviewRequired": classification
                in {"CORE_REVIEW", "SUPPORT_REVIEW"},
            }
        )

    seed_count = sum(len(value) for value in seeds.values())
    closure_paths = set().union(*closures.values()) if closures else set()
    partition_total = sum(classification_counts.values())
    source_count = len(audit.get("unregisteredFieldCandidates") or [])
    material = {
        "schema": SCHEMA,
        "mode": "report_only",
        "selectionHash": selection.get("selectionHash"),
        "graphHash": selection.get("graphHash"),
        "repositoryScanHash": audit.get("repositoryScanHash"),
        "classificationPolicy": {
            "KEEP_CORE": "CORE_REVIEW",
            "KEEP_SUPPORT": "SUPPORT_REVIEW",
            "ISOLATE": "ISOLATE_DEFER",
            "unclaimed": "OUTSIDE_CONTEST_SCOPE",
        },
        "registeredSeeds": {
            key: sorted(value) for key, value in sorted(seeds.items())
        },
        "closurePaths": {
            key: sorted(value) for key, value in sorted(closures.items())
        },
        "summary": {
            "sourceCandidateCount": source_count,
            "partitionCandidateCount": partition_total,
            "registeredPathSeedCount": seed_count,
            "closurePathCount": len(closure_paths),
            "contestReviewCandidateCount": (
                classification_counts.get("CORE_REVIEW", 0)
                + classification_counts.get("SUPPORT_REVIEW", 0)
            ),
            "classificationCounts": {
                key: int(classification_counts.get(key, 0))
                for key in _CLASSIFICATION_ORDER
            },
        },
        "pathCounts": [
            {"classification": classification, "path": path, "count": count}
            for (classification, path), count in sorted(
                path_counts.items(),
                key=lambda item: (item[0][0], item[0][1]),
            )
        ],
        "candidates": candidates,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    if partition_total != source_count:
        raise RuntimeError(
            f"FIELD_SCOPE_PARTITION_MISMATCH:{partition_total}:{source_count}"
        )
    return {**material, "scopeHash": _canonical_hash(material)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scope unregistered field candidates to the selected contest closure."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated", default="governance/contest/generated")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).resolve()
    generated = (root / args.generated).resolve()
    review = build_review(root, generated)
    _write_json(generated / "unregistered-field-scope-review.json", review)
    (generated / "unregistered-field-scope-review.md").write_text(
        _render_markdown(review),
        encoding="utf-8",
    )
    receipt_material = {
        "schema": "contest.field_scope_receipt.v1",
        "scopeHash": review["scopeHash"],
        "selectionHash": review.get("selectionHash"),
        "repositoryScanHash": review.get("repositoryScanHash"),
        "sourceCandidateCount": review["summary"]["sourceCandidateCount"],
        "contestReviewCandidateCount": review["summary"][
            "contestReviewCandidateCount"
        ],
        "states": [
            "REGISTERED_PATHS_SEEDED",
            "IMPORT_CLOSURE_BUILT",
            "FIELD_CANDIDATES_PARTITIONED",
            "REVIEW_SCOPE_LOCKED",
        ],
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    receipt = {
        **receipt_material,
        "receiptHash": _canonical_hash(receipt_material),
    }
    _write_json(generated / "field-scope-receipt.json", receipt)
    print(
        json.dumps(
            {
                "scopeHash": review["scopeHash"],
                "receiptHash": receipt["receiptHash"],
                "summary": review["summary"],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
