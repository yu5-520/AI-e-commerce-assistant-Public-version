"""Migrate approved legacy keys using sealed and complete AST evidence."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

REVIEW_HASH = "sha256:f1efe99fa15eca67e9c2c306f635f2431a34890832a7eefd22c33e11723d0e99"
GRAPH_HASH = "sha256:95cc643fcf7b468bf86e1dbbfd65fe29fc40392849aa2b752a07413226902d00"
SELECTION_HASH = "sha256:d6d19db916506c22542bbf68e01871330eae0efd110d9abd9a9d05d82e071c7e"
EXPECTED_FILES = 14
EXPECTED_OUTSIDE = 42
EXPECTED_SEALED_TOTAL = 25
EXPECTED_COMPLETE_TOTAL = 29
MAPPINGS = {
    "payload.selectedActionFamilyHint": {
        "old": "selectedActionFamilyHint",
        "new": "lockedActionFamily",
        "fieldId": "agent1.locked_action_family",
        "canonicalPath": "lockedActionFamily",
        "sealedCount": 19,
        "completeAstCount": 19,
    },
    "payload.creativeTestPlan": {
        "old": "creativeTestPlan",
        "new": "creativeDraft",
        "fieldId": "agent2.creative_draft",
        "canonicalPath": "agent2ActionDraft.creativeDraft",
        "sealedCount": 6,
        "completeAstCount": 10,
    },
}


def read(path: Path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def content_hash(text: str):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def pattern(key: str):
    return re.compile(r"(?P<q>['\"])" + re.escape(key) + r"(?P=q)")


def ast_occurrences(source: str, key: str, filename: str):
    tree = ast.parse(source, filename=filename)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    result = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value == key
        ):
            continue
        owner = parents.get(node)
        result.append({
            "line": int(getattr(node, "lineno", 0) or 0),
            "column": int(getattr(node, "col_offset", 0) or 0),
            "parentNode": type(owner).__name__ if owner is not None else None,
        })
    return sorted(result, key=lambda item: (item["line"], item["column"], str(item["parentNode"])))


def registry_gate(root: Path):
    fields = {
        item.get("fieldId"): item
        for item in read(root / "contracts/registry/fields.json").get("fields") or []
        if isinstance(item, dict)
    }
    for mapping in MAPPINGS.values():
        field = fields.get(mapping["fieldId"])
        if not field or field.get("status") != "ACTIVE":
            raise RuntimeError(f"REPLACEMENT_NOT_ACTIVE:{mapping['fieldId']}")
        if field.get("canonicalPath") != mapping["canonicalPath"]:
            raise RuntimeError(f"CANONICAL_PATH_MISMATCH:{mapping['fieldId']}")


def approved_refs(generated: Path):
    consolidated = read(generated / "contest-consolidated-review.json")
    if consolidated.get("state") != "REVIEW_READY_FOR_HUMAN_APPROVAL":
        raise RuntimeError("REVIEW_NOT_READY")
    if consolidated.get("consolidatedReviewHash") != REVIEW_HASH:
        raise RuntimeError("REVIEW_HASH_MISMATCH")
    approved = set(
        consolidated.get("approvedNextTransactionScope", {}).get("migrateLegacyFieldIds") or []
    )
    if approved != set(MAPPINGS):
        raise RuntimeError(f"APPROVED_SCOPE_MISMATCH:{sorted(approved)}")

    review = read(generated / "tombstone-scope-review.json")
    refs = [
        dict(item) for item in review.get("references") or []
        if isinstance(item, dict)
        and item.get("classification") == "CORE_REVIEW"
        and item.get("legacyPath") in MAPPINGS
    ]
    counts = Counter(item["legacyPath"] for item in refs)
    expected = {key: value["sealedCount"] for key, value in MAPPINGS.items()}
    if dict(counts) != expected:
        raise RuntimeError(f"SEALED_REFERENCE_COUNT_MISMATCH:{dict(counts)}")
    if sum(counts.values()) != EXPECTED_SEALED_TOTAL:
        raise RuntimeError("SEALED_REFERENCE_TOTAL_MISMATCH")
    if len({item["path"] for item in refs}) != EXPECTED_FILES:
        raise RuntimeError("SOURCE_FILE_COUNT_MISMATCH")
    return refs


def supplemental_occurrences(occurrences, sealed_items):
    sealed_lines = Counter(int(item.get("line") or 0) for item in sealed_items)
    available = Counter(item["line"] for item in occurrences)
    for line, count in sealed_lines.items():
        if available[line] < count:
            raise RuntimeError(f"SEALED_LINE_NOT_IN_AST:{line}:{count}:{available[line]}")
        available[line] -= count
    supplemental = []
    for item in occurrences:
        if available[item["line"]] > 0:
            supplemental.append(item)
            available[item["line"]] -= 1
    return supplemental


def apply(root: Path, generated: Path):
    registry_gate(root)
    refs = approved_refs(generated)
    grouped = defaultdict(lambda: defaultdict(list))
    for item in refs:
        grouped[item["path"]][item["legacyPath"]].append(item)

    files = []
    complete_counts = Counter()
    supplemental_counts = Counter()
    for relative, per_key in sorted(grouped.items()):
        path = root / relative
        before = path.read_text(encoding="utf-8")
        current = before
        replacements = []
        for legacy, sealed_items in sorted(per_key.items()):
            mapping = MAPPINGS[legacy]
            occurrences = ast_occurrences(before, mapping["old"], relative)
            literal_count = len(pattern(mapping["old"]).findall(before))
            if literal_count != len(occurrences):
                raise RuntimeError(f"LITERAL_AST_COUNT_MISMATCH:{relative}:{legacy}")
            if len(occurrences) < len(sealed_items):
                raise RuntimeError(f"AST_BELOW_SEALED_COUNT:{relative}:{legacy}")
            supplemental = supplemental_occurrences(occurrences, sealed_items)
            current, changed = pattern(mapping["old"]).subn(
                lambda match: f"{match.group('q')}{mapping['new']}{match.group('q')}", current
            )
            if changed != len(occurrences):
                raise RuntimeError(f"APPLY_COUNT_MISMATCH:{relative}:{legacy}:{changed}")
            complete_counts[legacy] += changed
            supplemental_counts[legacy] += len(supplemental)
            replacements.append({
                "legacyPath": legacy,
                "legacyKey": mapping["old"],
                "replacementKey": mapping["new"],
                "replacementFieldId": mapping["fieldId"],
                "sealedOccurrenceCount": len(sealed_items),
                "completeAstOccurrenceCount": len(occurrences),
                "supplementalOccurrenceCount": len(supplemental),
                "sealedLines": sorted(int(item.get("line") or 0) for item in sealed_items),
                "completeAstOccurrences": occurrences,
                "supplementalAstOccurrences": supplemental,
                "sealedOperations": dict(Counter(str(item.get("operation") or "") for item in sealed_items)),
            })
        ast.parse(current, filename=relative)
        for replacement in replacements:
            if ast_occurrences(current, replacement["legacyKey"], relative):
                raise RuntimeError(f"LEGACY_LITERAL_REMAINS:{relative}:{replacement['legacyKey']}")
        path.write_text(current, encoding="utf-8")
        files.append({
            "path": relative,
            "beforeContentHash": content_hash(before),
            "afterContentHash": content_hash(current),
            "replacements": replacements,
        })

    expected_complete = {key: value["completeAstCount"] for key, value in MAPPINGS.items()}
    expected_supplemental = {
        key: value["completeAstCount"] - value["sealedCount"]
        for key, value in MAPPINGS.items()
    }
    if dict(complete_counts) != expected_complete:
        raise RuntimeError(f"COMPLETE_AST_TOTAL_MISMATCH:{dict(complete_counts)}")
    if dict(supplemental_counts) != expected_supplemental:
        raise RuntimeError(f"SUPPLEMENTAL_TOTAL_MISMATCH:{dict(supplemental_counts)}")

    material = {
        "schema": "contest.core_field_migration_plan.v2",
        "state": "EXACT_CORE_FIELD_MIGRATION_APPLIED_PENDING_RECOMPILE",
        "approvalEvidence": {
            "consolidatedReviewHash": REVIEW_HASH,
            "userInstruction": "continue_execute",
            "sealedReferenceCount": EXPECTED_SEALED_TOTAL,
            "supplementalAstReferenceCount": EXPECTED_COMPLETE_TOTAL - EXPECTED_SEALED_TOTAL,
        },
        "graphHash": GRAPH_HASH,
        "selectionHash": SELECTION_HASH,
        "sourceFileCount": len(files),
        "sealedReferenceCount": EXPECTED_SEALED_TOTAL,
        "supplementalAstReferenceCount": EXPECTED_COMPLETE_TOTAL - EXPECTED_SEALED_TOTAL,
        "migratedReferenceCount": sum(complete_counts.values()),
        "completeAstCounts": dict(sorted(complete_counts.items())),
        "supplementalAstCounts": dict(sorted(supplemental_counts.items())),
        "expectedOutsideReferenceCountPreserved": EXPECTED_OUTSIDE,
        "mappings": MAPPINGS,
        "files": files,
        "sourceCodeModified": True,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "deploymentExecuted": False,
        "mainMutated": False,
        "physicalDeletionExecuted": False,
        "publicReleaseExecuted": False,
    }
    plan = {**material, "transactionHash": digest(material)}
    write(generated / "core-field-migration-plan.json", plan)
    return {
        "transactionHash": plan["transactionHash"],
        "changedFileCount": len(files),
        "sealedReferenceCount": EXPECTED_SEALED_TOTAL,
        "supplementalAstReferenceCount": EXPECTED_COMPLETE_TOTAL - EXPECTED_SEALED_TOTAL,
        "migratedReferenceCount": plan["migratedReferenceCount"],
    }


def verify(root: Path, generated: Path):
    plan = read(generated / "core-field-migration-plan.json")
    selection = read(generated / "contest-selection-manifest.json")
    audit = read(generated / "repository-audit.json")
    tombstones = read(generated / "tombstone-scope-review.json")
    summary = tombstones.get("summary") or {}
    counts = summary.get("classificationCounts") or {}
    assertions = {
        "graphHashStable": selection.get("graphHash") == GRAPH_HASH,
        "selectionHashStable": selection.get("selectionHash") == SELECTION_HASH,
        "runnerDriftCountZero": int(audit.get("summary", {}).get("runnerDriftCount") or 0) == 0,
        "coreLegacyReferencesZero": int(counts.get("CORE_REVIEW") or 0) == 0,
        "supportLegacyReferencesZero": int(counts.get("SUPPORT_REVIEW") or 0) == 0,
        "outsideCompatibilityReferencesPreserved": int(counts.get("OUTSIDE_CONTEST_SCOPE") or 0) == EXPECTED_OUTSIDE,
        "sourceReferencesReducedToOutsideOnly": int(summary.get("sourceReferenceCount") or 0) == EXPECTED_OUTSIDE,
        "partitionComplete": summary.get("sourceReferenceCount") == summary.get("partitionReferenceCount"),
        "blockedKeysZero": int(summary.get("contestDeletionBlockedKeyCount") or 0) == 0,
        "sourceFileCountLocked": int(plan.get("sourceFileCount") or 0) == EXPECTED_FILES,
        "sealedEvidenceCountLocked": int(plan.get("sealedReferenceCount") or 0) == EXPECTED_SEALED_TOTAL,
        "supplementalEvidenceCountLocked": int(plan.get("supplementalAstReferenceCount") or 0) == 4,
        "completeMigrationCountLocked": int(plan.get("migratedReferenceCount") or 0) == EXPECTED_COMPLETE_TOTAL,
    }
    if not all(assertions.values()):
        raise RuntimeError("MIGRATION_VERIFY_FAILED:" + json.dumps(assertions, sort_keys=True))
    for item in plan.get("files") or []:
        relative = item["path"]
        source = (root / relative).read_text(encoding="utf-8")
        ast.parse(source, filename=relative)
        for replacement in item.get("replacements") or []:
            if ast_occurrences(source, replacement["legacyKey"], relative):
                raise RuntimeError(f"POST_MIGRATION_LEGACY_REMAINS:{relative}")

    material = {
        "schema": "contest.core_field_migration_review.v2",
        "state": "CORE_LEGACY_FIELD_MIGRATION_VERIFIED_PRUNE_SIMULATION_READY",
        "transactionHash": plan.get("transactionHash"),
        "supersedesConsolidatedReviewHash": REVIEW_HASH,
        "graphHash": GRAPH_HASH,
        "selectionHash": SELECTION_HASH,
        "migratedLegacyPaths": sorted(MAPPINGS),
        "replacementFieldIds": sorted(value["fieldId"] for value in MAPPINGS.values()),
        "sourceFileChangeCount": EXPECTED_FILES,
        "sealedCoreReferenceCount": EXPECTED_SEALED_TOTAL,
        "supplementalAstReferenceCount": 4,
        "migratedCoreReferenceCount": EXPECTED_COMPLETE_TOTAL,
        "preservedOutsideReferenceCount": EXPECTED_OUTSIDE,
        "assertions": assertions,
        "sourceCodeModified": True,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "deploymentExecuted": False,
        "mainMutated": False,
        "physicalDeletionExecuted": False,
        "publicReleaseExecuted": False,
    }
    review = {**material, "migrationReviewHash": digest(material)}
    receipt_material = {
        "schema": "contest.core_field_migration_receipt.v2",
        "state": review["state"],
        "transactionHash": plan.get("transactionHash"),
        "migrationReviewHash": review["migrationReviewHash"],
        "states": [
            "HUMAN_CONTINUE_APPROVAL_RECORDED",
            "SEALED_REFERENCE_SET_LOCKED",
            "AST_SUPPLEMENTAL_EVIDENCE_LOCKED",
            "EXACT_LITERAL_MIGRATION_APPLIED",
            "REGISTRY_AND_LINEAGE_RECOMPILED",
            "RUNNER_GATE_VERIFIED",
            "OUTSIDE_COMPATIBILITY_REFERENCES_PRESERVED",
            "PRUNE_SIMULATION_READY",
        ],
        "sourceCodeModified": True,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "deploymentExecuted": False,
        "mainMutated": False,
        "physicalDeletionExecuted": False,
        "publicReleaseExecuted": False,
    }
    receipt = {**receipt_material, "receiptHash": digest(receipt_material)}
    write(generated / "core-field-migration-review.json", review)
    write(generated / "core-field-migration-receipt.json", receipt)
    return {
        "state": review["state"],
        "transactionHash": plan.get("transactionHash"),
        "migrationReviewHash": review["migrationReviewHash"],
        "receiptHash": receipt["receiptHash"],
        "assertions": assertions,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "verify"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated", default="governance/contest/generated")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    generated = (root / args.generated).resolve()
    result = apply(root, generated) if args.command == "apply" else verify(root, generated)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
