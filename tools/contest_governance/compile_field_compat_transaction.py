"""Compile a report-only compatibility transaction for blocking tombstone fields."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Set

MAPPINGS = {
    "selectedActionFamilyHint": {
        "legacyPath": "payload.selectedActionFamilyHint",
        "replacementFieldId": "agent1.locked_action_family",
        "replacementKey": "lockedActionFamily",
    },
    "creativeTestPlan": {
        "legacyPath": "payload.creativeTestPlan",
        "replacementFieldId": "agent2.creative_draft",
        "replacementKey": "creativeDraft",
    },
}


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _parent_map(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    result: Dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _kind(node: ast.Constant, parent: ast.AST | None) -> str:
    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == "get":
        if parent.args and parent.args[0] is node:
            return "READ_GET"
    if isinstance(parent, ast.Subscript) and parent.slice is node:
        return "WRITE_SUBSCRIPT" if isinstance(parent.ctx, ast.Store) else "READ_SUBSCRIPT"
    if isinstance(parent, ast.Dict) and node in parent.keys:
        return "WRITE_DICT_LITERAL"
    if isinstance(parent, (ast.Compare, ast.List, ast.Tuple, ast.Set)):
        return "KEY_MEMBERSHIP_OR_COMPARE"
    return "REVIEW_REQUIRED"


def compile_transaction(root: Path) -> Dict[str, Any]:
    tombstone = _read(root / "governance/contest/generated/tombstone-scope-review.json")
    references = tombstone.get("references") or []
    core_paths: Set[str] = {
        str(item.get("path") or "")
        for item in references
        if isinstance(item, dict)
        and item.get("classification") in {"CORE_REVIEW", "SUPPORT_REVIEW"}
        and str(item.get("legacyPath") or "") in {v["legacyPath"] for v in MAPPINGS.values()}
    }

    occurrences: List[Dict[str, Any]] = []
    counts: MutableMapping[str, int] = {}
    for relative in sorted(core_paths):
        path = root / relative
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        parents = _parent_map(tree)
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value not in MAPPINGS:
                continue
            mapping = MAPPINGS[node.value]
            kind = _kind(node, parents.get(node))
            counts[kind] = counts.get(kind, 0) + 1
            occurrences.append({
                "path": relative,
                "line": int(getattr(node, "lineno", 0)),
                "column": int(getattr(node, "col_offset", 0)),
                "legacyKey": node.value,
                "legacyPath": mapping["legacyPath"],
                "replacementKey": mapping["replacementKey"],
                "replacementFieldId": mapping["replacementFieldId"],
                "kind": kind,
                "lineText": lines[node.lineno - 1].strip() if node.lineno else "",
            })

    material = {
        "schema": "contest.field_compat_transaction.v1",
        "mode": "report_only",
        "sourceReviewHash": tombstone.get("scopeHash"),
        "mappings": MAPPINGS,
        "corePathCount": len(core_paths),
        "occurrenceCount": len(occurrences),
        "classificationCounts": dict(sorted(counts.items())),
        "occurrences": sorted(occurrences, key=lambda x: (x["path"], x["line"], x["column"])),
        "automaticApplyAuthorized": counts.get("REVIEW_REQUIRED", 0) == 0,
        "writePolicy": "NEW_KEY_ONLY",
        "readPolicy": "NEW_KEY_THEN_LEGACY_KEY",
        "physicalDeletionAuthorized": False,
        "mainMutationAuthorized": False,
    }
    return {**material, "transactionHash": _hash(material)}


def main() -> int:
    root = Path.cwd().resolve()
    output = root / "governance/contest/generated/field-compat-transaction.json"
    result = compile_transaction(root)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "transactionHash": result["transactionHash"],
        "occurrenceCount": result["occurrenceCount"],
        "classificationCounts": result["classificationCounts"],
        "automaticApplyAuthorized": result["automaticApplyAuthorized"],
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
