"""Scope tombstone references through the verified contest import closure."""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

from tools.contest_governance.scope_unregistered_fields import (
    _canonical_hash,
    _candidate_classification,
    _closure,
    _read_object,
    _registered_seeds,
    _write_json,
)


SCHEMA = "contest.tombstone_scope_review.v1"
_CLASSIFICATION_ORDER = (
    "CORE_REVIEW",
    "SUPPORT_REVIEW",
    "ISOLATE_DEFER",
    "OUTSIDE_CONTEST_SCOPE",
)


def _recommendation(counts: Mapping[str, int]) -> str:
    if int(counts.get("CORE_REVIEW", 0)) > 0:
        return "KEEP_IN_CONTEST_AND_MIGRATE"
    if int(counts.get("SUPPORT_REVIEW", 0)) > 0:
        return "KEEP_SUPPORT_AND_MIGRATE"
    if int(counts.get("ISOLATE_DEFER", 0)) > 0:
        return "ISOLATE_DEFER"
    return "OUTSIDE_CONTEST_SCOPE_NOT_BLOCKING"


def _render_markdown(report: Mapping[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    counts = dict(summary.get("classificationCounts") or {})
    lines = [
        "# Tombstone Scope Review",
        "",
        f"- Scope hash: `{report.get('scopeHash')}`",
        f"- Source reference count: {summary.get('sourceReferenceCount', 0)}",
        f"- Contest review reference count: {summary.get('contestReviewReferenceCount', 0)}",
        f"- Registered path seed count: {summary.get('registeredPathSeedCount', 0)}",
        f"- Import-closure path count: {summary.get('closurePathCount', 0)}",
        "",
        "## Classification",
        "",
    ]
    for classification in _CLASSIFICATION_ORDER:
        lines.append(f"- {classification}: {counts.get(classification, 0)}")
    lines.extend(
        [
            "",
            "## Tombstone decisions",
            "",
            "| Legacy path | References | Core | Support | Isolate | Outside | Recommendation |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in report.get("tombstoneDecisions") or []:
        item_counts = dict(item.get("classificationCounts") or {})
        lines.append(
            "| `{legacy}` | {total} | {core} | {support} | {isolate} | {outside} | `{recommendation}` |".format(
                legacy=item.get("legacyPath"),
                total=item.get("referenceCount"),
                core=item_counts.get("CORE_REVIEW", 0),
                support=item_counts.get("SUPPORT_REVIEW", 0),
                isolate=item_counts.get("ISOLATE_DEFER", 0),
                outside=item_counts.get("OUTSIDE_CONTEST_SCOPE", 0),
                recommendation=item.get("recommendation"),
            )
        )
    lines.extend(
        [
            "",
            "## Gate",
            "",
            "`KEEP_IN_CONTEST_AND_MIGRATE` references block physical field deletion. References",
            "outside the selected closure do not block contest-chain pruning, but remain preserved",
            "until their owning files are separately isolated or removed by an approved transaction.",
            "",
            "This report performs no runtime, database, provider, or physical deletion action.",
            "",
        ]
    )
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

    references: List[Dict[str, Any]] = []
    classification_counts: Counter[str] = Counter()
    decision_records: MutableMapping[str, List[Dict[str, Any]]] = defaultdict(list)
    path_counts: MutableMapping[Tuple[str, str], int] = defaultdict(int)

    for raw in audit.get("retiredFieldCandidateHits") or []:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "")
        candidate = dict(raw.get("candidate") or {})
        legacy_path = str(candidate.get("legacyPath") or raw.get("key") or "")
        scopes = {
            classification
            for classification, paths in closures.items()
            if path in paths
        }
        classification = _candidate_classification(scopes)
        classification_counts[classification] += 1
        path_counts[(classification, path)] += 1
        record = {
            **raw,
            "legacyPath": legacy_path,
            "classification": classification,
            "registeredScopes": sorted(scopes),
            "scopeOrigins": {
                scope: origins.get(scope, {}).get(path)
                for scope in sorted(scopes)
            },
            "contestReviewRequired": classification
            in {"CORE_REVIEW", "SUPPORT_REVIEW"},
        }
        references.append(record)
        decision_records[legacy_path].append(record)

    decisions: List[Dict[str, Any]] = []
    for legacy_path, items in sorted(decision_records.items()):
        counts = Counter(str(item.get("classification") or "") for item in items)
        decisions.append(
            {
                "legacyPath": legacy_path,
                "referenceCount": len(items),
                "pathCount": len({str(item.get("path") or "") for item in items}),
                "classificationCounts": {
                    key: int(counts.get(key, 0)) for key in _CLASSIFICATION_ORDER
                },
                "recommendation": _recommendation(counts),
                "contestDeletionBlocked": bool(
                    counts.get("CORE_REVIEW", 0)
                    or counts.get("SUPPORT_REVIEW", 0)
                ),
            }
        )

    seed_count = sum(len(value) for value in seeds.values())
    closure_paths = set().union(*closures.values()) if closures else set()
    source_count = len(audit.get("retiredFieldCandidateHits") or [])
    partition_count = sum(classification_counts.values())
    if source_count != partition_count:
        raise RuntimeError(
            f"TOMBSTONE_SCOPE_PARTITION_MISMATCH:{partition_count}:{source_count}"
        )

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
            "sourceReferenceCount": source_count,
            "partitionReferenceCount": partition_count,
            "registeredPathSeedCount": seed_count,
            "closurePathCount": len(closure_paths),
            "contestReviewReferenceCount": (
                classification_counts.get("CORE_REVIEW", 0)
                + classification_counts.get("SUPPORT_REVIEW", 0)
            ),
            "classificationCounts": {
                key: int(classification_counts.get(key, 0))
                for key in _CLASSIFICATION_ORDER
            },
            "tombstoneKeyCount": len(decisions),
            "contestDeletionBlockedKeyCount": sum(
                1 for item in decisions if item["contestDeletionBlocked"]
            ),
        },
        "pathCounts": [
            {"classification": classification, "path": path, "count": count}
            for (classification, path), count in sorted(
                path_counts.items(),
                key=lambda item: (item[0][0], item[0][1]),
            )
        ],
        "tombstoneDecisions": decisions,
        "references": references,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    return {**material, "scopeHash": _canonical_hash(material)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scope tombstone references to the selected contest closure."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated", default="governance/contest/generated")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).resolve()
    generated = (root / args.generated).resolve()
    review = build_review(root, generated)
    _write_json(generated / "tombstone-scope-review.json", review)
    (generated / "tombstone-scope-review.md").write_text(
        _render_markdown(review),
        encoding="utf-8",
    )
    receipt_material = {
        "schema": "contest.tombstone_scope_receipt.v1",
        "scopeHash": review["scopeHash"],
        "selectionHash": review.get("selectionHash"),
        "repositoryScanHash": review.get("repositoryScanHash"),
        "sourceReferenceCount": review["summary"]["sourceReferenceCount"],
        "contestReviewReferenceCount": review["summary"][
            "contestReviewReferenceCount"
        ],
        "contestDeletionBlockedKeyCount": review["summary"][
            "contestDeletionBlockedKeyCount"
        ],
        "states": [
            "REGISTERED_PATHS_SEEDED",
            "IMPORT_CLOSURE_BUILT",
            "TOMBSTONE_REFERENCES_PARTITIONED",
            "TOMBSTONE_REVIEW_SCOPE_LOCKED",
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
    _write_json(generated / "tombstone-scope-receipt.json", receipt)
    print(
        json.dumps(
            {
                "scopeHash": review["scopeHash"],
                "receiptHash": receipt["receiptHash"],
                "summary": review["summary"],
                "tombstoneDecisions": review["tombstoneDecisions"],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
