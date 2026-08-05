"""Apply the authorized dual-read/new-write field compatibility transaction."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Sequence, Tuple

from tools.contest_governance.compile_field_compat_transaction import MAPPINGS, _kind, _parent_map


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _sha(data: str) -> str:
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def _line_offsets(source: str) -> List[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _span(node: ast.AST, offsets: Sequence[int]) -> Tuple[int, int]:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        raise RuntimeError(f"NODE_POSITION_REQUIRED:{type(node).__name__}")
    start = offsets[int(node.lineno) - 1] + int(node.col_offset)
    end = offsets[int(node.end_lineno) - 1] + int(node.end_col_offset)
    return start, end


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _edit_for_occurrence(
    source: str,
    offsets: Sequence[int],
    node: ast.Constant,
    parent: ast.AST | None,
    kind: str,
) -> Tuple[int, int, str, str] | None:
    mapping = MAPPINGS[str(node.value)]
    new_key = str(mapping["replacementKey"])
    old_key = str(node.value)
    node_start, node_end = _span(node, offsets)

    if kind == "READ_GET":
        if not isinstance(parent, ast.Call) or not isinstance(parent.func, ast.Attribute):
            raise RuntimeError("READ_GET_PARENT_INVALID")
        call_start, call_end = _span(parent, offsets)
        receiver = ast.get_source_segment(source, parent.func.value)
        if not receiver:
            raise RuntimeError("READ_GET_RECEIVER_MISSING")
        replacement = (
            f"({receiver}.get({_quoted(new_key)}) or "
            f"{receiver}.get({_quoted(old_key)}))"
        )
        return call_start, call_end, replacement, "dual_read_get"

    if kind in {"WRITE_DICT_LITERAL", "KEY_NAME_MAPPING_VALUE", "WRITE_SETDEFAULT", "WRITE_SUBSCRIPT"}:
        return node_start, node_end, _quoted(new_key), "new_key_write"

    if kind == "KEY_MEMBERSHIP_OR_COMPARE":
        if isinstance(parent, (ast.List, ast.Tuple, ast.Set)):
            replacement = f"{_quoted(new_key)}, {_quoted(old_key)}"
            return node_start, node_end, replacement, "dual_key_collection"
        if isinstance(parent, ast.Compare):
            compare_start, compare_end = _span(parent, offsets)
            original = source[compare_start:compare_end]
            relative_start = node_start - compare_start
            relative_end = node_end - compare_start
            modern = original[:relative_start] + _quoted(new_key) + original[relative_end:]
            return compare_start, compare_end, f"({modern} or {original})", "dual_key_compare"
        raise RuntimeError(f"KEY_MEMBERSHIP_PARENT_UNSUPPORTED:{type(parent).__name__}")

    if kind == "LEGACY_KEY_CLEANUP_POP":
        return None

    if kind == "READ_SUBSCRIPT":
        if not isinstance(parent, ast.Subscript):
            raise RuntimeError("READ_SUBSCRIPT_PARENT_INVALID")
        sub_start, sub_end = _span(parent, offsets)
        receiver = ast.get_source_segment(source, parent.value)
        if not receiver:
            raise RuntimeError("READ_SUBSCRIPT_RECEIVER_MISSING")
        replacement = (
            f"({receiver}.get({_quoted(new_key)}) or "
            f"{receiver}[{_quoted(old_key)}])"
        )
        return sub_start, sub_end, replacement, "dual_read_subscript"

    raise RuntimeError(f"UNAUTHORIZED_OCCURRENCE_KIND:{kind}")


def _apply_file(path: Path) -> Dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())
    parents = _parent_map(tree)
    offsets = _line_offsets(source)
    edits: List[Tuple[int, int, str, str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value not in MAPPINGS:
            continue
        parent = parents.get(node)
        kind = _kind(node, parent)
        edit = _edit_for_occurrence(source, offsets, node, parent, kind)
        if edit is None:
            continue
        start, end, replacement, operation = edit
        edits.append((start, end, replacement, operation, str(node.value)))

    ordered = sorted(edits, key=lambda item: (item[0], item[1]))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise RuntimeError(f"OVERLAPPING_EDITS:{path}:{previous}:{current}")

    result = source
    for start, end, replacement, _operation, _legacy in reversed(ordered):
        result = result[:start] + replacement + result[end:]

    ast.parse(result, filename=path.as_posix())
    if result != source:
        path.write_text(result, encoding="utf-8")

    return {
        "path": path.as_posix(),
        "beforeHash": _sha(source),
        "afterHash": _sha(result),
        "changed": result != source,
        "editCount": len(ordered),
        "edits": [
            {
                "operation": operation,
                "legacyKey": legacy,
                "replacementKey": MAPPINGS[legacy]["replacementKey"],
            }
            for _start, _end, _replacement, operation, legacy in ordered
        ],
    }


def _verify_legacy_usage(path: Path) -> List[Dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())
    parents = _parent_map(tree)
    violations: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value not in MAPPINGS:
            continue
        kind = _kind(node, parents.get(node))
        if kind in {"LEGACY_KEY_CLEANUP_POP", "READ_GET", "KEY_MEMBERSHIP_OR_COMPARE"}:
            continue
        violations.append({
            "path": path.as_posix(),
            "line": int(getattr(node, "lineno", 0)),
            "legacyKey": node.value,
            "kind": kind,
        })
    return violations


def main() -> int:
    root = Path.cwd().resolve()
    transaction = _read(root / "governance/contest/generated/field-compat-transaction.json")
    if transaction.get("automaticApplyAuthorized") is not True:
        raise SystemExit("AUTOMATIC_APPLY_NOT_AUTHORIZED")
    if int(transaction.get("reviewRequiredCount") or 0) != 0:
        raise SystemExit("REVIEW_REQUIRED_OCCURRENCES_REMAIN")

    paths = sorted({str(item.get("path") or "") for item in transaction.get("occurrences") or []})
    file_reports = [_apply_file(root / relative) for relative in paths]
    violations: List[Dict[str, Any]] = []
    for relative in paths:
        violations.extend(_verify_legacy_usage(root / relative))
    if violations:
        raise SystemExit("POST_APPLY_LEGACY_WRITE_VIOLATIONS:" + json.dumps(violations, ensure_ascii=False))

    material: Dict[str, Any] = {
        "schema": "contest.field_compat_apply_receipt.v1",
        "state": "FIELD_COMPATIBILITY_APPLIED_PENDING_TESTS",
        "sourceTransactionHash": transaction.get("transactionHash"),
        "mappings": transaction.get("mappings"),
        "fileCount": len(file_reports),
        "changedFileCount": sum(1 for item in file_reports if item["changed"]),
        "editCount": sum(int(item["editCount"]) for item in file_reports),
        "files": file_reports,
        "postApplyViolationCount": 0,
        "writePolicy": "NEW_KEY_ONLY",
        "readPolicy": "NEW_KEY_THEN_LEGACY_KEY",
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
        "mainMutated": False,
    }
    material["receiptHash"] = _sha(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    output = root / "governance/contest/generated/field-compat-apply-receipt.json"
    output.write_text(json.dumps(material, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "state": material["state"],
        "fileCount": material["fileCount"],
        "changedFileCount": material["changedFileCount"],
        "editCount": material["editCount"],
        "receiptHash": material["receiptHash"],
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
