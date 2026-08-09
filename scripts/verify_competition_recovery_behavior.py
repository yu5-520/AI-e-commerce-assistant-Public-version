#!/usr/bin/env python3
"""Verify runtime behavior for the misplaced competition-fix recovery plan.

This verifier consumes the existing deterministic three-report E2E attestation and
adds one isolated Agent1 fault probe. It does not modify runtime state, does not call a
real model, and does not unlock any repair scope. Its job is to separate requirements
that are already behaviorally correct from requirements that still need a scoped H2
runtime change.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "competition.recovery_behavior_verification.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON_OBJECT_REQUIRED:{path}")
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
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _artifact_refs(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("artifact_refs_json")
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _fault_isolation_probe(source_root: Path) -> dict[str, Any]:
    """Inject one hash-mismatched sibling and prove the other siblings survive.

    The legacy business normalizer is replaced with a transparent pass-through only
    inside this process. This isolates the V22.5.9 exact-identity filter so the probe
    tests precisely the behavior recovered from the misplaced mother-repo repair:
    same productId across three stores, one bad hash, no sibling cascade.
    """

    root_text = str(source_root.resolve())
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    from src.services import real_product_judgment_agent_v2259_service as agent  # noqa: E402

    original_normalize = agent.legacy._normalize_judgments
    original_source_maps = agent.legacy._source_maps

    products = []
    raw_judgments = []
    stores = ("TB-SH-001", "JD-SH-002", "DY-SH-003")
    for index, store_id in enumerate(stores, 1):
        execution_id = f"EXE-P10007-{index}"
        input_hash = f"sha256:input-p10007-{index}"
        product = {
            "correlationId": f"CORR-P10007-{index}",
            "productId": "P10007",
            "storeId": store_id,
            "signalId": f"SIG-P10007-{index}",
            "_hashExecution": {
                "itemExecutionId": execution_id,
                "inputContentHash": input_hash,
                "executionHash": f"sha256:execution-p10007-{index}",
                "inputArtifactRef": f"ART-P10007-{index}",
            },
        }
        products.append(product)
        raw_judgments.append(
            {
                "itemExecutionId": execution_id,
                "inputContentHash": (
                    "sha256:intentionally-wrong"
                    if store_id == "JD-SH-002"
                    else input_hash
                ),
                "correlationId": product["correlationId"],
                "productId": product["productId"],
                "storeId": product["storeId"],
                "signalId": product["signalId"],
                "decisionType": "observe",
            }
        )

    try:
        agent.legacy._source_maps = lambda _products: {}
        agent.legacy._normalize_judgments = (
            lambda provider_payload, _source_maps, _data_version: (
                [dict(item) for item in provider_payload.get("judgments") or []],
                {"probeLegacyNormalizer": "transparent_pass_through"},
            )
        )
        normalized, diagnostics = agent._normalize_judgments(
            {"judgments": raw_judgments},
            products,
            "DV-RECOVERY-FAULT-PROBE",
        )
    finally:
        agent.legacy._normalize_judgments = original_normalize
        agent.legacy._source_maps = original_source_maps

    accepted_ids = sorted(
        str(item.get("itemExecutionId") or "")
        for item in normalized
        if isinstance(item, dict) and item.get("itemExecutionId")
    )
    mismatches = [
        item
        for item in diagnostics.get("inputContentHashMismatches") or []
        if isinstance(item, dict)
    ]
    mismatch_ids = sorted(
        str(item.get("itemExecutionId") or "") for item in mismatches
    )
    missing_ids = sorted(str(item) for item in diagnostics.get("missingItemExecutionIds") or [])

    assertions = {
        "sameProductThreeStores": len({item["productId"] for item in products}) == 1
        and len({item["storeId"] for item in products}) == 3,
        "twoValidSiblingsAccepted": accepted_ids
        == ["EXE-P10007-1", "EXE-P10007-3"],
        "badSiblingRejectedOnly": mismatch_ids == ["EXE-P10007-2"]
        and missing_ids == ["EXE-P10007-2"],
        "fallbackIdentityDisabled": diagnostics.get("fallbackIdentityMatchingAllowed")
        is False,
        "noDuplicateConfusion": diagnostics.get("duplicateItemExecutionIds") in ([], None),
        "noExtraIdentityConfusion": diagnostics.get("extraItemExecutionIds") in ([], None),
    }
    return {
        "schema": "competition.agent1_fault_isolation_probe.v1",
        "verified": all(assertions.values()),
        "productId": "P10007",
        "stores": list(stores),
        "acceptedItemExecutionIds": accepted_ids,
        "hashMismatchItemExecutionIds": mismatch_ids,
        "missingItemExecutionIds": missing_ids,
        "assertions": assertions,
    }


def _single_worker_source_probe(source_root: Path) -> dict[str, Any]:
    path = source_root / "src/services/agent_token_runtime_hash_exact_v2259_service.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    function: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_agent1_projected_inputs":
            function = node
            break
    if function is None:
        return {
            "verified": False,
            "error": "run_agent1_projected_inputs_missing",
        }

    segment = ast.get_source_segment(source, function) or ""
    forbidden = (
        "ThreadPoolExecutor",
        "ProcessPoolExecutor",
        "asyncio.gather",
        "asyncio.create_task",
        "create_task(",
        "as_completed(",
    )
    present = [symbol for symbol in forbidden if symbol in segment]
    assertions = {
        "noFanoutPrimitive": not present,
        "sequentialBatchLoopPresent": "for batch_index, entries in enumerate(batches)" in segment,
    }
    return {
        "schema": "competition.agent1_single_worker_source_probe.v1",
        "verified": all(assertions.values()),
        "path": path.relative_to(source_root).as_posix(),
        "forbiddenFanoutSymbolsPresent": present,
        "assertions": assertions,
    }


def _lineage_artifact_probe(attestation: Mapping[str, Any]) -> dict[str, Any]:
    database = _dict(attestation.get("databaseEvidence"))
    rows = [item for item in _list(database.get("pipelineItems")) if isinstance(item, dict)]
    acting_rows = [row for row in rows if str(row.get("action_family") or "")]
    observed_rows = [
        row for row in rows if str(row.get("current_stage") or "") == "observed_soft_gate"
    ]

    acting_required = {
        "signalRef",
        "admissionRef",
        "agent1InputRef",
        "agent1Ref",
        "agent2DraftInputRef",
        "agent2DraftRef",
        "agent3SopInputRef",
        "agent3SopRef",
        "taskMappingRef",
        "taskAdmissionRef",
    }
    observed_required = {
        "signalRef",
        "admissionRef",
        "agent1InputRef",
        "agentExecutionInputRef",
        "agentExecutionOutputRef",
        "observationRef",
    }

    acting_evidence = []
    for row in acting_rows:
        refs = _artifact_refs(row)
        missing = sorted(key for key in acting_required if not str(refs.get(key) or "").startswith("ART-"))
        acting_evidence.append(
            {
                "itemId": row.get("item_id"),
                "productId": row.get("product_id"),
                "storeId": row.get("store_id"),
                "missingRefs": missing,
            }
        )

    observed_evidence = []
    for row in observed_rows:
        refs = _artifact_refs(row)
        missing = sorted(key for key in observed_required if not str(refs.get(key) or "").startswith("ART-"))
        observed_evidence.append(
            {
                "itemId": row.get("item_id"),
                "productId": row.get("product_id"),
                "storeId": row.get("store_id"),
                "missingRefs": missing,
            }
        )

    views = _dict(attestation.get("views"))
    status = _dict(views.get("pipelineStatus"))
    live = _dict(views.get("pipelineLive"))
    assertions = {
        "actingArtifactsPersisted": len(acting_evidence) >= 2
        and all(not item["missingRefs"] for item in acting_evidence),
        "observationArtifactsPersisted": len(observed_evidence) >= 1
        and all(not item["missingRefs"] for item in observed_evidence),
        "statusReadsPipelineArtifactRefs": status.get("runtimeSource")
        == "pipeline_items.artifact_refs_json",
        "productTruthUsesArtifactRefs": "artifactRefs"
        in str(live.get("productTruthSource") or ""),
        "payloadInferenceDisabled": live.get("payloadRead") is False,
        "alternateRuntimeDisabled": status.get("alternateRuntimeAllowed") is False,
    }
    return {
        "schema": "competition.agent_lineage_artifact_probe.v1",
        "verified": all(assertions.values()),
        "actingItems": acting_evidence,
        "observedItems": observed_evidence,
        "pipelineStatusRuntimeSource": status.get("runtimeSource"),
        "pipelineLiveProductTruthSource": live.get("productTruthSource"),
        "assertions": assertions,
    }


def build_report(
    *,
    attestation: Mapping[str, Any],
    recovery: Mapping[str, Any],
    source_root: Path,
) -> dict[str, Any]:
    if attestation.get("verified") is not True:
        raise RuntimeError("THREE_REPORT_E2E_NOT_VERIFIED")

    views = _dict(attestation.get("views"))
    status = _dict(views.get("pipelineStatus"))
    background = _dict(status.get("backgroundWorker"))
    binding = _dict(background.get("activeAgent1RuntimeBinding"))
    fresh_probe = _dict(attestation.get("freshUploadProbe"))
    handoff_probe = _dict(attestation.get("workerHandoffProbe"))
    fault_probe = _fault_isolation_probe(source_root)
    single_worker_source = _single_worker_source_probe(source_root)
    lineage_probe = _lineage_artifact_probe(attestation)

    provider_stages = _dict(_dict(attestation.get("replayCheck")).get("before")).get("stageCounts")
    provider_stages = _dict(provider_stages)

    rec003_assertions = {
        "batchIdentityExact": status.get("batchItemIdentity")
        == "itemExecutionId+inputContentHash",
        "fallbackDisabled": status.get("fallbackAllowed") is False,
        "cachedRebindingDisabled": status.get("cachedOutputRebindingAllowed") is False,
        "onlyTrueMissingRetries": status.get("onlyTrueMissingItemsRetry") is True,
        "hashRuntimeActive": _dict(status.get("runtimeVersions")).get(
            "activeAgent1TokenImplementation"
        )
        == "src.services.agent_token_runtime_hash_exact_v2259_service",
        "agent1ActuallyCalled": int(provider_stages.get("product_judgment_agent") or 0) >= 1,
    }
    rec005_assertions = {
        "singleAppWorker": binding.get("secondWorkerCreated") is False,
        "secondWorkerDisallowed": background.get("secondWorkerAllowed") is False,
        "activeBindingMatched": binding.get("matched") is True,
        "sourceHasNoFanout": single_worker_source.get("verified") is True,
        "agent2AfterAgent1EvidenceExists": int(provider_stages.get("product_judgment_agent") or 0) >= 1
        and int(provider_stages.get("action_plan_judgment_agent") or 0) >= 1,
    }

    requirements = {
        "REC-001": {
            "status": "deferred_to_era_sample_contract",
            "verified": None,
            "reason": "The three-report Agent fixture contains three signal products, not the 10x3 ERA sample inventory. Keep REC-001 on the separate ERA/canonical contract evidence rather than manufacturing a false 30-unit assertion here.",
        },
        "REC-002": {
            "status": "behavior_verified" if fresh_probe.get("verified") is True else "behavior_failed",
            "verified": fresh_probe.get("verified") is True,
            "evidence": fresh_probe,
        },
        "REC-003": {
            "status": "behavior_verified" if all(rec003_assertions.values()) else "behavior_failed",
            "verified": all(rec003_assertions.values()),
            "assertions": rec003_assertions,
        },
        "REC-004": {
            "status": "behavior_verified" if fault_probe.get("verified") is True else "behavior_failed",
            "verified": fault_probe.get("verified") is True,
            "evidence": fault_probe,
        },
        "REC-005": {
            "status": "behavior_verified" if all(rec005_assertions.values()) else "behavior_failed",
            "verified": all(rec005_assertions.values()),
            "assertions": rec005_assertions,
            "sourceProbe": single_worker_source,
        },
        "REC-006": {
            "status": "behavior_verified" if lineage_probe.get("verified") is True else "behavior_failed",
            "verified": lineage_probe.get("verified") is True,
            "evidence": lineage_probe,
        },
        "REC-007": {
            "status": (
                "explicit_tick_path_verified_autonomous_handoff_not_claimed"
                if handoff_probe.get("verified") is True
                else "behavior_failed"
            ),
            "verified": handoff_probe.get("verified") is True,
            "evidence": handoff_probe,
            "scope": "explicit /api/system/run-agent-pipeline-tick path only",
        },
        "REC-008": {
            "status": "do_not_migrate",
            "verified": True,
            "reason": "Mother-repo deployment implementation remains outside competition recovery scope.",
        },
    }

    required_behavior_ids = ("REC-002", "REC-003", "REC-004", "REC-005", "REC-006", "REC-007", "REC-008")
    failed = [
        requirement_id
        for requirement_id in required_behavior_ids
        if requirements[requirement_id].get("verified") is not True
    ]
    material = {
        "schema": SCHEMA,
        "sourceCommit": attestation.get("sourceCommit"),
        "threeReportVerificationHash": attestation.get("verificationHash"),
        "recoveryManifestVersion": recovery.get("version"),
        "requiredBehaviorIds": list(required_behavior_ids),
        "requirements": requirements,
        "failedRequirementIds": failed,
    }
    return {
        **material,
        "verified": not failed,
        "behaviorHash": _hash(material),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify competition recovery behavior over the deterministic E2E evidence."
    )
    parser.add_argument(
        "--attestation",
        default="dist/competition-three-report-e2e/three-report-e2e-attestation.json",
    )
    parser.add_argument(
        "--recovery",
        default="governance/competition_misrouted_fix_recovery_v1.json",
    )
    parser.add_argument("--source-root", default=".")
    parser.add_argument(
        "--output",
        default="dist/competition-three-report-e2e/recovery-behavior-verification.json",
    )
    parser.add_argument("--allow-failures", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        attestation=_read(Path(args.attestation)),
        recovery=_read(Path(args.recovery)),
        source_root=Path(args.source_root).resolve(),
    )
    _write(Path(args.output), report)
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "sourceCommit": report["sourceCommit"],
                "behaviorHash": report["behaviorHash"],
                "failedRequirementIds": report["failedRequirementIds"],
                "requirements": {
                    key: value.get("status")
                    for key, value in report["requirements"].items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["verified"] or args.allow_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
