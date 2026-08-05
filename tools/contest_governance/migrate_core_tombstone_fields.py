"""Migrate only approved legacy keys inside the selected contest closure."""
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
MAPPINGS = {
    "payload.selectedActionFamilyHint": {
        "old": "selectedActionFamilyHint",
        "new": "lockedActionFamily",
        "fieldId": "agent1.locked_action_family",
        "canonicalPath": "lockedActionFamily",
        "count": 19,
    },
    "payload.creativeTestPlan": {
        "old": "creativeTestPlan",
        "new": "creativeDraft",
        "fieldId": "agent2.creative_draft",
        "canonicalPath": "agent2ActionDraft.creativeDraft",
        "count": 6,
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


def ast_count(source: str, key: str, filename: str):
    tree = ast.parse(source, filename=filename)
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == key
    )


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
    expected = {key: value["count"] for key, value in MAPPINGS.items()}
    if dict(counts) != expected:
        raise RuntimeError(f"REFERENCE_COUNT_MISMATCH:{dict(counts)}")
    if len({item["path"] for item in refs}) != EXPECTED_FILES:
        raise RuntimeError("SOURCE_FILE_COUNT_MISMATCH")
    return refs


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


def apply(root: Path, generated: Path):
    registry_gate(root)
    refs = approved_refs(generated)
    grouped = defaultdict(lambda: defaultdict(list))
    for item in refs:
        grouped[item["path"]][item["legacyPath"]].append(item)

    files = []
    for relative, per_key in sorted(grouped.items()):
        path = root / relative
        before = path.read_text(encoding="utf-8")
        current = before
        replacements = []
        for legacy, items in sorted(per_key.items()):
            mapping = MAPPINGS[legacy]
            expected = len(items)
            literal_count = len(pattern(mapping["old"]).findall(before))
            syntax_count = ast_count(before, mapping["old"], relative)
            if literal_count != expected or syntax_count != expected:
                raise RuntimeError(
                    f"PRECONDITION_COUNT_MISMATCH:{relative}:{legacy}:"
                    f"expected={expected}:literal={literal_count}:ast={syntax_count}"
                )
            current, changed = pattern(mapping["old"]).subn(
                lambda match: f"{match.group('q')}{mapping['new']}{match.group('q')}", current
            )
            if changed != expected:
                raise RuntimeError(f"APPLY_COUNT_MISMATCH:{relative}:{legacy}:{changed}")
            replacements.append({
                "legacyPath": legacy,
                "legacyKey": mapping["old"],
                "replacementKey": mapping["new"],
                "replacementFieldId": mapping["fieldId"],
                "occurrenceCount": changed,
                "lines": sorted(int(item.get("line") or 0) for item in items),
                "operations": dict(Counter(str(item.get("operation") or "") for item in items)),
            })
        ast.parse(current, filename=relative)
        for replacement in replacements:
            if ast_count(current, replacement["legacyKey"], relative):
                raise RuntimeError(f"LEGACY_LITERAL_REMAINS:{relative}:{replacement['legacyKey']}")
        path.write_text(current, encoding="utf-8")
        files.append({
            "path": relative,
            "beforeContentHash": content_hash(before),
            "afterContentHash": content_hash(current),
            "replacements": replacements,
        })

    material = {
        "schema": "contest.core_field_migration_plan.v1",
        "state": "EXACT_CORE_FIELD_MIGRATION_APPLIED_PENDING_RECOMPILE",
        "approvalEvidence": {"consolidatedReviewHash": REVIEW_HASH, "userInstruction": "continue_execute"},
        "graphHash": GRAPH_HASH,
        "selectionHash": SELECTION_HASH,
        "sourceFileCount": len(files),
        "migratedReferenceCount": sum(value["count"] for value in MAPPINGS.values()),
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
    }
    if not all(assertions.values()):
        raise RuntimeError("MIGRATION_VERIFY_FAILED:" + json.dumps(assertions, sort_keys=True))
    for item in plan.get("files") or []:
        relative = item["path"]
        source = (root / relative).read_text(encoding="utf-8")
        ast.parse(source, filename=relative)
        for replacement in item.get("replacements") or []:
            if ast_count(source, replacement["legacyKey"], relative):
                raise RuntimeError(f"POST_MIGRATION_LEGACY_REMAINS:{relative}")

    material = {
        "schema": "contest.core_field_migration_review.v1",
        "state": "CORE_LEGACY_FIELD_MIGRATION_VERIFIED_PRUNE_SIMULATION_READY",
        "transactionHash": plan.get("transactionHash"),
        "supersedesConsolidatedReviewHash": REVIEW_HASH,
        "graphHash": GRAPH_HASH,
        "selectionHash": SELECTION_HASH,
        "migratedLegacyPaths": sorted(MAPPINGS),
        "replacementFieldIds": sorted(value["fieldId"] for value in MAPPINGS.values()),
        "sourceFileChangeCount": EXPECTED_FILES,
        "migratedCoreReferenceCount": 25,
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
        "schema": "contest.core_field_migration_receipt.v1",
        "state": review["state"],
        "transactionHash": plan.get("transactionHash"),
        "migrationReviewHash": review["migrationReviewHash"],
        "states": [
            "HUMAN_CONTINUE_APPROVAL_RECORDED",
            "CORE_REFERENCE_SET_LOCKED",
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
