#!/usr/bin/env python3
"""Run the existing fixed three-report E2E under the V26.9 candidate graph contract.

The base E2E still owns packaging, deployment, real pipeline HTTP calls, replay checks
and frontend checks. This wrapper removes the retired single action-family-column
assertion and adds V26.9 scheduler/graph invariants to the same attestation.

Scheduler-stage proof is based on durable pipeline Artifact references rather than a
transient tick response. A stage may execute inside the single station worker without
being surfaced as the outer HTTP tick's selectedStage; its persisted output Artifact is
the authoritative proof that the stage actually completed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
for path in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import run_competition_three_report_e2e_history_warmup as warmup  # noqa: E402
from src.services import v269_semantic_graph_service as graphs  # noqa: E402


def _canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _graph_probe() -> dict[str, Any]:
    facts = {"fact:roi": {"value": 2.0, "unit": "ratio"}}
    raw = {
        "nodes": [
            {
                "nodeKey": "J1",
                "kind": "JudgementNode",
                "reasoning": "结构验收判断",
                "evidenceRefs": ["fact:roi"],
                "confidence": 0.9,
            },
            {
                "nodeKey": "DA_TRAFFIC",
                "kind": "DecisionActionNode",
                "judgementRefs": ["J1"],
                "actionType": "controlled_scale",
                "actionFamily": "roas_scale",
                "priority": 0.9,
                "confidence": 0.85,
                "evidenceRefs": ["fact:roi"],
            },
            {
                "nodeKey": "DA_CREATIVE",
                "kind": "DecisionActionNode",
                "judgementRefs": ["J1"],
                "actionType": "creative_test",
                "actionFamily": "title_image_test",
                "priority": 0.7,
                "confidence": 0.8,
                "evidenceRefs": ["fact:roi"],
            },
        ],
        "edges": [
            {"sourceRef": "J1", "targetRef": "DA_TRAFFIC", "relation": "supports"},
            {"sourceRef": "J1", "targetRef": "DA_CREATIVE", "relation": "supports"},
            {"sourceRef": "DA_TRAFFIC", "targetRef": "DA_CREATIVE", "relation": "enables"},
        ],
    }
    decision = graphs.compile_graph(
        "DecisionGraph", raw, evidence_refs=["fact:roi"], fact_values=facts
    )
    admission = graphs.admit_actions(decision, ["DA_TRAFFIC", "DA_CREATIVE"])
    partitions = graphs.partition_actions(decision, admission)

    conflict_raw = deepcopy(raw)
    conflict_raw["edges"] = [
        {"sourceRef": "J1", "targetRef": "DA_TRAFFIC", "relation": "supports"},
        {"sourceRef": "J1", "targetRef": "DA_CREATIVE", "relation": "supports"},
        {"sourceRef": "DA_TRAFFIC", "targetRef": "DA_CREATIVE", "relation": "conflicts_with"},
    ]
    conflict = graphs.compile_graph(
        "DecisionGraph", conflict_raw, evidence_refs=["fact:roi"], fact_values=facts
    )
    conflict_admission = graphs.admit_actions(
        conflict, ["DA_TRAFFIC", "DA_CREATIVE"]
    )

    clean_source = {"DecisionGraph": decision}
    poisoned_source = {
        "DecisionGraph": decision,
        "lockedActionFamily": "forged",
        "executionLock": {"forged": True},
        "primaryAction": "forged",
        "familyPayload": {"forged": True},
    }
    clean_projection = graphs.graph_input("agent2", clean_source)
    poisoned_projection = graphs.graph_input("agent2", poisoned_source)
    poison_invariant = (
        poisoned_projection == clean_projection
        and set(poisoned_projection) == {"DecisionGraph"}
        and poisoned_projection["DecisionGraph"] == decision
    )
    contract = graphs.contract()
    assertions = {
        "multiDomainPartitioned": [item["domain"] for item in partitions] == ["creative", "traffic"],
        "dependencyPreserved": admission["admitted"] == ["DA_TRAFFIC", "DA_CREATIVE"],
        "conflictRejected": conflict_admission["admitted"] == []
        and set(conflict_admission["deferred"].values()) == {"CONFLICT_REQUIRES_RESOLUTION"},
        "legacyPoisonIgnored": poison_invariant,
        "registeredGraphRuntimeActive": contract.get("rolloutStatus") == "active",
    }
    return {
        "schema": "v269.three_report_graph_probe.v1",
        "assertions": assertions,
        "partitionDomains": [item["domain"] for item in partitions],
        "partitionHashes": [item["receiptHash"] for item in partitions],
        "conflictAdmission": conflict_admission,
        "legacyPoisonProjectionHash": _canonical_hash(poisoned_projection),
        "cleanProjectionHash": _canonical_hash(clean_projection),
        "rolloutStatus": contract.get("rolloutStatus"),
        "verified": all(assertions.values()),
    }


def _stage_count(database: dict[str, Any], stage: str) -> int:
    return sum(
        int(item.get("count") or 0)
        for item in database.get("pipelineStages") or []
        if isinstance(item, dict) and item.get("current_stage") == stage
    )


def _artifact_refs(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("artifact_refs_json")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _durable_scheduler_stages(database: dict[str, Any]) -> set[str]:
    """Infer completed scheduler stages from immutable downstream Artifact receipts."""
    stages: set[str] = set()
    for item in database.get("pipelineItems") or []:
        if not isinstance(item, dict):
            continue
        refs = _artifact_refs(item)
        if refs.get("agent2DraftRef"):
            stages.add("v269_decision_graph_to_partitioned_plan_graph")
        if refs.get("agent3SopRef"):
            stages.add("v269_plan_graph_to_operation_graph")
        if refs.get("taskMappingRef"):
            stages.add("v269_operation_graph_to_task_mapping")
        if refs.get("taskAdmissionRef") or refs.get("taskAdmissionFailureRef"):
            stages.add("v269_task_mapped_to_atomic_task_pool")
    return stages


def _augment(output: Path) -> dict[str, Any]:
    report = json.loads(output.read_text(encoding="utf-8"))
    tick_stages = {
        str(item.get("selectedStage") or "")
        for item in report.get("ticks") or []
        if isinstance(item, dict) and item.get("selectedStage")
    }
    required_scheduler_stages = {
        "v269_decision_graph_to_partitioned_plan_graph",
        "v269_plan_graph_to_operation_graph",
        "v269_operation_graph_to_task_mapping",
        "v269_task_mapped_to_atomic_task_pool",
    }
    provider = ((report.get("replayCheck") or {}).get("before") or {})
    provider_stages = provider.get("stageCounts") if isinstance(provider.get("stageCounts"), dict) else {}
    database = report.get("databaseEvidence") if isinstance(report.get("databaseEvidence"), dict) else {}
    durable_stages = _durable_scheduler_stages(database)
    probe = _graph_probe()
    assertions = {
        "baseThreeReportE2EVerified": report.get("verified") is True,
        "allV269SchedulerStagesProven": required_scheduler_stages.issubset(durable_stages),
        "agent1GraphProviderCalled": int(provider_stages.get("product_judgment_agent") or 0) >= 1,
        "agent2GraphProviderCalled": int(provider_stages.get("action_plan_judgment_agent") or 0) >= 1,
        "agent3GraphProviderCalled": int(provider_stages.get("agent3_sop_agent") or 0) >= 1,
        "observedTerminalPresent": _stage_count(database, "observed_soft_gate") >= 1,
        "graphTaskAdmitted": _stage_count(database, "task_admitted") >= 1,
        "terminalReplayNoProviderCall": int((report.get("replayCheck") or {}).get("additionalProviderCalls") or 0) == 0,
        "graphStructureProbeVerified": probe["verified"] is True,
        "realModelQualityStillSeparate": report.get("realBailianRunStillRequired") is True,
    }
    verified = all(assertions.values())
    report["v269GraphContract"] = {
        "schema": "competition.v269_graph_contract_e2e.v1",
        "assertions": assertions,
        "requiredSchedulerStages": sorted(required_scheduler_stages),
        "durablyProvenSchedulerStages": sorted(durable_stages),
        "tickObservedSchedulerStages": sorted(tick_stages),
        "schedulerProofAuthority": "pipeline_item_artifact_refs",
        "graphProbe": probe,
        "legacySingleActionFamilyAssertionDisabled": True,
        "experienceRolloutStatus": json.loads((REPO_ROOT / "rag/manifest/v269b_manifest.json").read_text())["rolloutStatus"],
        "promotionRolloutStatus": json.loads((REPO_ROOT / "rag/manifest/v269c_manifest.json").read_text())["rolloutStatus"],
        "evaluationContractVersion": json.loads((REPO_ROOT / "config/v269b_evaluation_contract.json").read_text())["version"],
        "experienceFeedbackLoopExercised": False,
        "verified": verified,
    }
    report["v269GraphContractVerified"] = verified
    if not verified:
        report["verified"] = False
        failed = [key for key, value in assertions.items() if value is not True]
        report.setdefault("errors", []).append("V269_GRAPH_ASSERTIONS_FAILED:" + ",".join(failed))
    material = {
        key: value
        for key, value in report.items()
        if key not in {"verificationHash", "verified"}
    }
    report["verificationHash"] = _canonical_hash(material)
    output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    if not verified:
        raise RuntimeError(report["errors"][-1])
    return report


def main(argv: Sequence[str] | None = None) -> int:
    os.environ["V269_CANDIDATE_GRAPH_RUNTIME"] = "1"
    args = warmup.base.parse_args(argv)
    original_read_scenario = warmup.base.read_scenario

    def read_v269_scenario(path: Path) -> dict[str, Any]:
        scenario = original_read_scenario(path)
        expected = dict(scenario.get("expected") or {})
        # action_family is a retired V22 pipeline column, not V26.9 task identity.
        expected["requiredActionFamilies"] = []
        scenario["expected"] = expected
        return scenario

    warmup.base.read_scenario = read_v269_scenario
    code = warmup.main(argv)
    report = _augment(Path(args.output).expanduser().resolve())
    print(
        json.dumps(
            {
                "verified": report.get("verified"),
                "v269GraphContractVerified": report.get("v269GraphContractVerified"),
                "sourceCommit": report.get("sourceCommit"),
                "latestDataVersion": report.get("latestDataVersion"),
                "verificationHash": report.get("verificationHash"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"V26.9 three-report E2E failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
