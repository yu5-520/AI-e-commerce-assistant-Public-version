"""Verify dual-read/new-write semantics without third-party test dependencies."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from tools.contest_governance.compile_field_compat_transaction import MAPPINGS, _parent_map


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _get_signature(call: ast.Call) -> tuple[str, str] | None:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "get" or not call.args:
        return None
    key = call.args[0]
    if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
        return None
    receiver = ast.dump(call.func.value, include_attributes=False)
    return receiver, key.value


def _contains_paired_get(node: ast.AST, receiver: str, old_key: str, new_key: str) -> bool:
    signatures = {
        signature
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        for signature in [_get_signature(item)]
        if signature is not None
    }
    return (receiver, old_key) in signatures and (receiver, new_key) in signatures


def _nearest_boolop(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> ast.BoolOp | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.BoolOp):
            return current
        if isinstance(current, (ast.Assign, ast.Return, ast.Call, ast.Dict, ast.ListComp, ast.comprehension)):
            # Calls are allowed only when the BoolOp is outside the nested get call.
            pass
        current = parents.get(current)
    return None


def verify_file(path: Path) -> Dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())
    parents = _parent_map(tree)
    violations: List[Dict[str, Any]] = []
    legacy_reads = 0
    legacy_cleanup = 0
    modern_writes = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        parent = parents.get(node)

        for old_key, mapping in MAPPINGS.items():
            new_key = str(mapping["replacementKey"])
            if node.value == new_key and isinstance(parent, ast.Dict) and node in parent.keys:
                modern_writes += 1

            if node.value != old_key:
                continue

            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
                if parent.args and parent.args[0] is node and parent.func.attr == "pop":
                    legacy_cleanup += 1
                    continue
                if parent.args and parent.args[0] is node and parent.func.attr == "get":
                    signature = _get_signature(parent)
                    boolop = _nearest_boolop(parent, parents)
                    if signature is None or boolop is None or not _contains_paired_get(
                        boolop, signature[0], old_key, new_key
                    ):
                        violations.append({
                            "path": path.as_posix(),
                            "line": node.lineno,
                            "kind": "UNPAIRED_LEGACY_GET",
                            "legacyKey": old_key,
                        })
                    else:
                        legacy_reads += 1
                    continue

            if isinstance(parent, ast.Dict):
                violations.append({
                    "path": path.as_posix(),
                    "line": node.lineno,
                    "kind": "LEGACY_DICT_WRITE_OR_MAPPING",
                    "legacyKey": old_key,
                })
                continue

            if isinstance(parent, (ast.List, ast.Tuple, ast.Set)):
                values = {
                    item.value
                    for item in parent.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
                if new_key not in values:
                    violations.append({
                        "path": path.as_posix(),
                        "line": node.lineno,
                        "kind": "UNPAIRED_LEGACY_COLLECTION_KEY",
                        "legacyKey": old_key,
                    })
                else:
                    legacy_reads += 1
                continue

            if isinstance(parent, ast.Compare):
                boolop = _nearest_boolop(parent, parents)
                if boolop is None:
                    violations.append({
                        "path": path.as_posix(),
                        "line": node.lineno,
                        "kind": "UNPAIRED_LEGACY_COMPARE",
                        "legacyKey": old_key,
                    })
                    continue
                constants = {
                    item.value
                    for item in ast.walk(boolop)
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                }
                if new_key not in constants:
                    violations.append({
                        "path": path.as_posix(),
                        "line": node.lineno,
                        "kind": "UNPAIRED_LEGACY_COMPARE",
                        "legacyKey": old_key,
                    })
                else:
                    legacy_reads += 1
                continue

            violations.append({
                "path": path.as_posix(),
                "line": int(getattr(node, "lineno", 0)),
                "kind": "UNAUTHORIZED_LEGACY_USAGE",
                "legacyKey": old_key,
                "parentType": type(parent).__name__ if parent is not None else None,
            })

    return {
        "path": path.as_posix(),
        "legacyReadCount": legacy_reads,
        "legacyCleanupCount": legacy_cleanup,
        "modernWriteCount": modern_writes,
        "violationCount": len(violations),
        "violations": violations,
    }


def main(argv: Sequence[str] | None = None) -> int:
    root = Path.cwd().resolve()
    transaction = _read(root / "governance/contest/generated/field-compat-transaction.json")
    paths = sorted({str(item.get("path") or "") for item in transaction.get("occurrences") or []})
    reports = [verify_file(root / relative) for relative in paths]
    violations = [item for report in reports for item in report["violations"]]
    result = {
        "schema": "contest.field_compat_semantic_verification.v1",
        "state": "FIELD_COMPATIBILITY_SEMANTICS_VERIFIED" if not violations else "FIELD_COMPATIBILITY_SEMANTICS_FAILED",
        "fileCount": len(reports),
        "legacyReadCount": sum(int(item["legacyReadCount"]) for item in reports),
        "legacyCleanupCount": sum(int(item["legacyCleanupCount"]) for item in reports),
        "modernWriteCount": sum(int(item["modernWriteCount"]) for item in reports),
        "violationCount": len(violations),
        "files": reports,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
        "mainMutated": False,
    }
    output = root / "governance/contest/generated/field-compat-semantic-verification.json"
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "state": result["state"],
        "fileCount": result["fileCount"],
        "legacyReadCount": result["legacyReadCount"],
        "legacyCleanupCount": result["legacyCleanupCount"],
        "modernWriteCount": result["modernWriteCount"],
        "violationCount": result["violationCount"],
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if not violations else 2


if __name__ == "__main__":
    raise SystemExit(main())
