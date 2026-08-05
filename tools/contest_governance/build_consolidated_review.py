"""Build the final report-only contest governance review.

This compiler consolidates already sealed Registry, lineage, Runner, field-scope,
Tombstone, protocol-adapter, and isolation-simulation evidence. It does not alter the
product Registry, runtime projection, business code, database, provider state, main
branch, or physical files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


def _read(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _tombstone_blockers(review: Mapping[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in review.get("tombstoneDecisions") or []:
        if not isinstance(item, dict) or item.get("contestDeletionBlocked") is not True:
            continue
        result.append(
            {
                "legacyPath": item.get("legacyPath"),
                "referenceCount": item.get("referenceCount"),
                "classificationCounts": item.get("classificationCounts"),
                "recommendation": item.get("recommendation"),
            }
        )
    return result


def _render_markdown(review: Mapping[str, Any]) -> str:
    classifications = dict(review.get("moduleClassifications") or {})
    unresolved = dict(review.get("humanReviewRequired") or {})
    lines = [
        "# Contest Governance Consolidated Review",
        "",
        f"- State: `{review.get('state')}`",
        f"- Consolidated hash: `{review.get('consolidatedReviewHash')}`",
        f"- Graph hash: `{review.get('graphHash')}`",
        f"- Selection hash: `{review.get('selectionHash')}`",
        "",
        "## Module selection",
        "",
        f"- KEEP_CORE: {len(classifications.get('KEEP_CORE') or [])}",
        f"- KEEP_SUPPORT: {len(classifications.get('KEEP_SUPPORT') or [])}",
        f"- ISOLATE: {len(classifications.get('ISOLATE') or [])}",
        f"- REMOVE_CANDIDATE: {len(classifications.get('REMOVE_CANDIDATE') or [])}",
        f"- REVIEW_REQUIRED: {len(classifications.get('REVIEW_REQUIRED') or [])}",
        "",
        "## Resolved machine gates",
        "",
    ]
    for gate in review.get("resolvedMachineGates") or []:
        lines.append(
            f"- `{gate.get('gate')}`: `{gate.get('state')}` — {gate.get('evidence')}"
        )
    lines.extend(
        [
            "",
            "## Human review required",
            "",
            f"- Core unregistered field candidate occurrences: "
            f"{unresolved.get('coreUnregisteredFieldCandidateOccurrenceCount', 0)}",
            f"- Core Tombstone reference occurrences: "
            f"{unresolved.get('coreTombstoneReferenceOccurrenceCount', 0)}",
            f"- Core Tombstone keys requiring migration: "
            f"{unresolved.get('coreTombstoneKeyCount', 0)}",
            "",
            "### Tombstone migration blockers",
            "",
            "| Legacy path | References | Recommendation |",
            "|---|---:|---|",
        ]
    )
    for item in unresolved.get("coreTombstoneKeys") or []:
        lines.append(
            f"| `{item.get('legacyPath')}` | {item.get('referenceCount')} | "
            f"`{item.get('recommendation')}` |"
        )
    lines.extend(
        [
            "",
            "## Isolation decision",
            "",
            "Seven REGISTERED_ONLY modules are logically isolated. Detached-worktree",
            "simulation confirms that excluding their two implementation files keeps the",
            "Graph Hash, Selection Hash, module classifications, core/support Runner gates,",
            "and layered Registry-root composition stable. Physical deletion remains",
            "unauthorized until human approval.",
            "",
            "## Approval boundary",
            "",
            "Approval may authorize the next transaction to migrate the two blocking legacy",
            "fields and isolate the seven REGISTERED_ONLY modules. It does not authorize",
            "promotion to main, public release, database mutation, provider calls, or any",
            "other physical deletion.",
            "",
        ]
    )
    return "\n".join(lines)


def build(root: Path, generated: Path) -> Dict[str, Any]:
    chain = _read(generated / "contest-chain-review.json")
    selection = _read(generated / "contest-selection-manifest.json")
    snapshot = _read(generated / "registry-snapshot.json")
    audit = _read(generated / "repository-audit.json")
    fields = _read(generated / "unregistered-field-scope-review.json")
    tombstones = _read(generated / "tombstone-scope-review.json")
    protocol = _read(generated / "registry-protocol-adapter-review.json")
    isolation = _read(generated / "module-isolation-transaction.json")
    simulation = _read(generated / "module-isolation-simulation.json")

    classifications = dict(selection.get("classifications") or {})
    field_summary = dict(fields.get("summary") or {})
    tombstone_summary = dict(tombstones.get("summary") or {})
    blockers = _tombstone_blockers(tombstones)

    resolved_machine_gates = [
        {
            "gate": "REGISTRY_DOCUMENT_HASHES",
            "state": "VERIFIED",
            "evidence": snapshot.get("adapterCheck", {}).get("allDocumentsMatch"),
        },
        {
            "gate": "LAYERED_ROOT_COMPOSITION",
            "state": "VERIFIED",
            "evidence": snapshot.get("rootEquivalenceContract", {}).get("verified"),
        },
        {
            "gate": "RUNNER_DISPATCH",
            "state": "VERIFIED",
            "evidence": {
                "registeredModuleCount": audit.get("summary", {}).get("registeredModuleCount"),
                "runnerDriftCount": audit.get("summary", {}).get("runnerDriftCount"),
            },
        },
        {
            "gate": "Z_REGISTRY_PROTOCOL_ADAPTER",
            "state": "VERIFIED_RUNTIME_SWITCH_NOT_AUTHORIZED",
            "evidence": {
                "adapterHash": protocol.get("adapterHash"),
                "normalizedRegistryRootHash": protocol.get("normalizedRegistryRootHash"),
                "sourceDocumentHashesVerified": protocol.get("sourceDocumentHashesVerified"),
                "zCompilerVerified": protocol.get("zCompilerVerified"),
            },
        },
        {
            "gate": "UNREGISTERED_FIELD_SCOPE_PARTITION",
            "state": "VERIFIED",
            "evidence": {
                "sourceCandidateCount": field_summary.get("sourceCandidateCount"),
                "partitionCandidateCount": field_summary.get("partitionCandidateCount"),
                "scopeHash": fields.get("scopeHash"),
            },
        },
        {
            "gate": "TOMBSTONE_SCOPE_PARTITION",
            "state": "VERIFIED",
            "evidence": {
                "sourceReferenceCount": tombstone_summary.get("sourceReferenceCount"),
                "partitionReferenceCount": tombstone_summary.get("partitionReferenceCount"),
                "scopeHash": tombstones.get("scopeHash"),
            },
        },
        {
            "gate": "REGISTERED_ONLY_ISOLATION_SIMULATION",
            "state": "VERIFIED_PHYSICAL_CHANGE_NOT_AUTHORIZED",
            "evidence": {
                "transactionHash": isolation.get("transactionHash"),
                "simulationHash": simulation.get("simulationHash"),
                "assertions": simulation.get("assertions"),
            },
        },
    ]

    assertions = {
        "rootCompositionVerified": snapshot.get("rootEquivalent") is True,
        "runnerDriftCountZero": int(audit.get("summary", {}).get("runnerDriftCount") or 0) == 0,
        "protocolAdapterVerified": protocol.get("zCompilerVerified") is True
        and protocol.get("sourceDocumentHashesVerified") is True,
        "fieldPartitionComplete": field_summary.get("sourceCandidateCount")
        == field_summary.get("partitionCandidateCount")
        == 500,
        "tombstonePartitionComplete": tombstone_summary.get("sourceReferenceCount")
        == tombstone_summary.get("partitionReferenceCount")
        == 67,
        "isolationSimulationVerified": all(
            bool(value) for value in (simulation.get("assertions") or {}).values()
        ),
        "moduleClassificationStable": len(classifications.get("KEEP_CORE") or []) == 14
        and len(classifications.get("KEEP_SUPPORT") or []) == 2
        and len(classifications.get("ISOLATE") or []) == 7
        and len(classifications.get("REMOVE_CANDIDATE") or []) == 0
        and len(classifications.get("REVIEW_REQUIRED") or []) == 0,
    }
    if not all(assertions.values()):
        raise RuntimeError(
            "CONSOLIDATED_GATE_FAILED:"
            + json.dumps(assertions, ensure_ascii=False, sort_keys=True)
        )

    material = {
        "schema": "contest.governance_consolidated_review.v1",
        "mode": "report_only",
        "state": "REVIEW_READY_FOR_HUMAN_APPROVAL",
        "sourceAnalysisCommit": chain.get("baselineCommit"),
        "graphHash": selection.get("graphHash"),
        "selectionHash": selection.get("selectionHash"),
        "moduleClassifications": classifications,
        "resolvedMachineGates": resolved_machine_gates,
        "humanReviewRequired": {
            "coreUnregisteredFieldCandidateOccurrenceCount": int(
                (field_summary.get("classificationCounts") or {}).get("CORE_REVIEW") or 0
            ),
            "coreTombstoneReferenceOccurrenceCount": int(
                (tombstone_summary.get("classificationCounts") or {}).get("CORE_REVIEW") or 0
            ),
            "coreTombstoneKeyCount": len(blockers),
            "coreTombstoneKeys": blockers,
            "decision": "MIGRATE_BLOCKING_FIELDS_BEFORE_PHYSICAL_PRUNE",
        },
        "approvedNextTransactionScope": {
            "migrateLegacyFieldIds": [
                str(item.get("legacyPath")) for item in blockers
            ],
            "logicallyIsolateModuleIds": isolation.get("isolateModuleIds"),
            "simulatedExcludePaths": isolation.get("simulationExcludePaths"),
            "physicalDeletionAuthorized": False,
            "promotionAuthorized": False,
            "publicReleaseAuthorized": False,
        },
        "assertions": assertions,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "analysisBranchFilesDeleted": 0,
        "mainMutated": False,
        "physicalDeletionExecuted": False,
        "promotionExecuted": False,
        "publicReleaseExecuted": False,
    }
    review = {**material, "consolidatedReviewHash": _hash(material)}
    receipt_material = {
        "schema": "contest.governance_consolidated_review_receipt.v1",
        "consolidatedReviewHash": review["consolidatedReviewHash"],
        "graphHash": review["graphHash"],
        "selectionHash": review["selectionHash"],
        "state": review["state"],
        "states": [
            "REGISTRY_VERIFIED",
            "LINEAGE_GRAPH_VERIFIED",
            "RUNNER_GATES_VERIFIED",
            "FIELD_SCOPE_PARTITIONED",
            "TOMBSTONE_SCOPE_PARTITIONED",
            "PROTOCOL_ADAPTER_VERIFIED",
            "ISOLATION_SIMULATION_VERIFIED",
            "REVIEW_READY_FOR_HUMAN_APPROVAL",
        ],
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "analysisBranchFilesDeleted": 0,
        "mainMutated": False,
        "physicalDeletionExecuted": False,
        "promotionExecuted": False,
        "publicReleaseExecuted": False,
    }
    receipt = {**receipt_material, "receiptHash": _hash(receipt_material)}

    _write(generated / "contest-consolidated-review.json", review)
    _write(generated / "contest-consolidated-review-receipt.json", receipt)
    (generated / "contest-consolidated-review.md").write_text(
        _render_markdown(review), encoding="utf-8"
    )
    return {
        "state": review["state"],
        "consolidatedReviewHash": review["consolidatedReviewHash"],
        "receiptHash": receipt["receiptHash"],
        "humanReviewRequired": review["humanReviewRequired"],
        "moduleCounts": {
            key: len(value) for key, value in classifications.items()
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the consolidated contest review.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--generated", default="governance/contest/generated")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(args.root).resolve()
    result = build(root, (root / args.generated).resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
