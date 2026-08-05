"""Classify field candidates by registered contest closure evidence.

The compiler consumes the sealed selection manifest and repository audit. Path ownership
comes only from registered module physical paths, registered Runner paths, and exact AST
call evidence. It never infers ownership from directory or filename naming conventions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Set


_CLASSIFICATION_PRIORITY = {
    "KEEP_CORE": 4,
    "KEEP_SUPPORT": 3,
    "ISOLATE": 2,
    "REVIEW_REQUIRED": 1,
}


def _read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _module_classifications(selection: Mapping[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for classification, raw_ids in (selection.get("classifications") or {}).items():
        for module_id in raw_ids or []:
            result[str(module_id)] = str(classification)
    return result


def _add_claim(
    claims: MutableMapping[str, List[Dict[str, Any]]],
    *,
    path: str,
    module_id: str,
    classification: str,
    evidence_type: str,
) -> None:
    normalized = str(path or "").strip().lstrip("./")
    if not normalized:
        return
    record = {
        "moduleId": str(module_id),
        "classification": str(classification),
        "evidenceType": str(evidence_type),
    }
    if record not in claims[normalized]:
        claims[normalized].append(record)


def _build_path_claims(
    selection: Mapping[str, Any],
    repository_audit: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    classifications = _module_classifications(selection)
    claims: MutableMapping[str, List[Dict[str, Any]]] = defaultdict(list)

    for item in selection.get("physicalPathReview") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        item_classification = str(item.get("classification") or "REVIEW_REQUIRED")
        module_ids = [str(value) for value in item.get("claimingModuleIds") or []]
        if not module_ids:
            module_ids = ["__selection_manifest__"]
        for module_id in module_ids:
            _add_claim(
                claims,
                path=path,
                module_id=module_id,
                classification=classifications.get(module_id, item_classification),
                evidence_type="registered_physical_path",
            )

    for module_id, raw_audit in (repository_audit.get("moduleAudits") or {}).items():
        if not isinstance(raw_audit, dict):
            continue
        classification = classifications.get(str(module_id), "REVIEW_REQUIRED")
        _add_claim(
            claims,
            path=str(raw_audit.get("runnerPath") or ""),
            module_id=str(module_id),
            classification=classification,
            evidence_type="registered_runner_path",
        )
        for evidence in raw_audit.get("dispatchEvidence") or []:
            if not isinstance(evidence, dict):
                continue
            _add_claim(
                claims,
                path=str(evidence.get("path") or ""),
                module_id=str(module_id),
                classification=classification,
                evidence_type="ast_dispatch_call_path",
            )

    result: Dict[str, Dict[str, Any]] = {}
    for path, raw_claims in sorted(claims.items()):
        ordered = sorted(
            raw_claims,
            key=lambda item: (
                -_CLASSIFICATION_PRIORITY.get(str(item["classification"]), 0),
                str(item["moduleId"]),
                str(item["evidenceType"]),
            ),
        )
        effective = str(ordered[0]["classification"])
        result[path] = {
            "path": path,
            "effectiveClassification": effective,
            "claims": ordered,
        }
    return result


def _scope_for_path(path: str, path_claims: Mapping[str, Any]) -> str:
    claim = path_claims.get(str(path or "").lstrip("./"))
    if not isinstance(claim, dict):
        return "OUTSIDE_REGISTERED_CONTEST_EVIDENCE"
    classification = str(claim.get("effectiveClassification") or "")
    if classification in {"KEEP_CORE", "KEEP_SUPPORT"}:
        return "IN_CONTEST_CLOSURE"
    if classification == "ISOLATE":
        return "IN_ISOLATED_REGISTERED_SCOPE"
    return "REVIEW_REQUIRED_SCOPE"


def _classify_hits(
    hits: Iterable[Any],
    path_claims: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for raw in hits:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").lstrip("./")
        scope = _scope_for_path(path, path_claims)
        claim = path_claims.get(path)
        result.append(
            {
                **raw,
                "path": path,
                "contestScope": scope,
                "pathClaim": claim,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            str(item.get("contestScope") or ""),
            str(item.get("path") or ""),
            int(item.get("line") or 0),
            str(item.get("key") or ""),
        ),
    )


def _scope_counts(records: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts: MutableMapping[str, int] = defaultdict(int)
    for item in records:
        counts[str(item.get("contestScope") or "UNKNOWN")] += 1
    return dict(sorted(counts.items()))


def _key_summary(records: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped: MutableMapping[str, List[Mapping[str, Any]]] = defaultdict(list)
    for item in records:
        key = str(item.get("key") or item.get("candidate", {}).get("legacyPath") or "")
        grouped[key].append(item)

    result: List[Dict[str, Any]] = []
    for key, items in sorted(grouped.items()):
        scopes = _scope_counts(items)
        paths = sorted(set(str(item.get("path") or "") for item in items))
        if scopes.get("IN_CONTEST_CLOSURE", 0):
            recommendation = "KEEP_AND_MIGRATE_REQUIRED"
        elif scopes.get("IN_ISOLATED_REGISTERED_SCOPE", 0):
            recommendation = "ISOLATE_THEN_REVIEW"
        else:
            recommendation = "OUTSIDE_CLOSURE_REVIEW_REQUIRED"
        result.append(
            {
                "key": key,
                "occurrenceCount": len(items),
                "uniquePathCount": len(paths),
                "paths": paths,
                "scopeCounts": scopes,
                "recommendation": recommendation,
            }
        )
    return result


def _render_markdown(report: Mapping[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    lines = [
        "# Contest Field Scope Review",
        "",
        f"- State: `{report.get('reviewState')}`",
        f"- Graph hash: `{report.get('graphHash')}`",
        f"- Selection hash: `{report.get('selectionHash')}`",
        f"- Repository scan hash: `{report.get('repositoryScanHash')}`",
        f"- Field scope hash: `{report.get('fieldScopeHash')}`",
        "",
        "## Scope summary",
        "",
        f"- Registered evidence paths: {summary.get('registeredEvidencePathCount', 0)}",
        f"- Tombstone occurrences: {summary.get('tombstoneOccurrenceCount', 0)}",
        f"- Unregistered candidate occurrences: {summary.get('unregisteredCandidateOccurrenceCount', 0)}",
        "",
        "### Tombstone scopes",
        "",
    ]
    for scope, count in sorted((summary.get("tombstoneScopeCounts") or {}).items()):
        lines.append(f"- `{scope}`: {count}")
    lines.extend(["", "### Unregistered candidate scopes", ""])
    for scope, count in sorted((summary.get("unregisteredScopeCounts") or {}).items()):
        lines.append(f"- `{scope}`: {count}")

    lines.extend(
        [
            "",
            "## Tombstone key decisions",
            "",
            "| Key | Occurrences | Paths | Recommendation |",
            "|---|---:|---:|---|",
        ]
    )
    for item in report.get("tombstoneKeyReview") or []:
        lines.append(
            f"| `{item.get('key')}` | {item.get('occurrenceCount')} | "
            f"{item.get('uniquePathCount')} | `{item.get('recommendation')}` |"
        )

    lines.extend(
        [
            "",
            "## Gate",
            "",
            "No field or file deletion is authorized by this report. Paths outside registered",
            "contest evidence remain review items until reverse dependency simulation passes.",
            "",
        ]
    )
    return "\n".join(lines)


def run(root: Path, output_dir: Path) -> Dict[str, Any]:
    selection = _read_object(root / "governance/contest/generated/contest-selection-manifest.json")
    audit = _read_object(root / "governance/contest/generated/repository-audit.json")
    path_claims = _build_path_claims(selection, audit)

    tombstones = _classify_hits(audit.get("retiredFieldCandidateHits") or [], path_claims)
    unregistered = _classify_hits(audit.get("unregisteredFieldCandidates") or [], path_claims)

    material = {
        "schema": "contest.field_scope_review.v1",
        "mode": "report_only",
        "graphHash": selection.get("graphHash"),
        "selectionHash": selection.get("selectionHash"),
        "repositoryScanHash": audit.get("repositoryScanHash"),
        "pathClaims": path_claims,
        "tombstoneReferences": tombstones,
        "unregisteredFieldCandidates": unregistered,
        "tombstoneKeyReview": _key_summary(tombstones),
        "summary": {
            "registeredEvidencePathCount": len(path_claims),
            "tombstoneOccurrenceCount": len(tombstones),
            "tombstoneScopeCounts": _scope_counts(tombstones),
            "unregisteredCandidateOccurrenceCount": len(unregistered),
            "unregisteredScopeCounts": _scope_counts(unregistered),
        },
        "reviewState": "REVIEW_PENDING",
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    field_scope_hash = _sha256(material)
    report = {**material, "fieldScopeHash": field_scope_hash}

    receipt_material = {
        "schema": "contest.field_scope_receipt.v1",
        "fieldScopeHash": field_scope_hash,
        "graphHash": selection.get("graphHash"),
        "selectionHash": selection.get("selectionHash"),
        "repositoryScanHash": audit.get("repositoryScanHash"),
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "physicalDeletionExecuted": False,
    }
    receipt = {**receipt_material, "receiptHash": _sha256(receipt_material)}

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "contest-field-scope-review.json", report)
    _write_json(output_dir / "contest-field-scope-receipt.json", receipt)
    (output_dir / "contest-field-scope-review.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    return {
        "fieldScopeHash": field_scope_hash,
        "receiptHash": receipt["receiptHash"],
        "summary": report["summary"],
        "tombstoneKeyReview": report["tombstoneKeyReview"],
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scope field candidates by contest closure evidence.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="governance/contest/generated")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).resolve()
    result = run(root, (root / args.output).resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
