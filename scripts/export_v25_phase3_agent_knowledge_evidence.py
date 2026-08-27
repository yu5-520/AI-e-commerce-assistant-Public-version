#!/usr/bin/env python3
"""Export V25.6-V25.9 Agent knowledge migration evidence without app bootstrap."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict


def canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("v25_unified_agent_knowledge_evidence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("v25_unified_agent_knowledge_module_spec_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fields(plan: Dict[str, Any]) -> set[str]:
    return {
        str(item.get("canonicalField"))
        for item in plan.get("fields") or []
        if isinstance(item, dict)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="dist/v25-phase3/agent-knowledge-migration-evidence.json")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    service_path = root / "src" / "services" / "unified_agent_knowledge_v25_service.py"
    bootstrap_path = root / "src" / "__init__.py"
    runtime_path = root / "src" / "services" / "v22_runtime_service.py"

    module = load_module(service_path)

    a1 = module.compose_knowledge_plan(
        "Agent1",
        {
            "metricCodes": ["ctr", "conversionRate"],
            "signalFlags": ["organic_traffic_decline"],
            "crossMetricRequired": True,
        },
    )
    a2 = module.compose_knowledge_plan(
        "Agent2",
        {"actionFamily": "roas_scale", "experienceRequired": True},
    )
    a3 = module.compose_knowledge_plan(
        "Agent3",
        {
            "customerFacing": True,
            "historicalCaseRequired": True,
            "experienceRequired": True,
        },
    )

    assert "metric.ctr.interpretation" in fields(a1)
    assert "traffic.organic.decline_causes" in fields(a1)
    assert "diagnosis.cross_metric.patterns" in fields(a1)
    assert "experience.positive.applicability" not in fields(a1)

    assert "action.roas.scale.strategy" in fields(a2)
    assert "experience.positive.applicability" in fields(a2)
    assert "experience.negative.risk" in fields(a2)
    assert "company.sop.execution_principles" not in fields(a2)

    assert "company.sop.execution_principles" in fields(a3)
    assert "company.sop.task_timing" in fields(a3)
    assert "brand.expression.style" in fields(a3)
    assert "company.sop.historical_cases" in fields(a3)

    agent1_context = module.build_agent1_unified_knowledge_context(
        {
            "principles": ["rule"],
            "guardrails": {"onePrimaryActionFamily": True},
            "experienceCards": [{"caseId": "LEGACY-A1-1"}],
        }
    )
    assert agent1_context["experienceCards"] == []
    assert agent1_context["unifiedKnowledge"]["legacyDirectExperienceDroppedCount"] == 1
    assert agent1_context["unifiedKnowledge"]["legacyDirectKnowledgeReadAllowed"] is False

    fake_snapshot = {
        "positiveExperienceCards": [
            {
                "caseId": "POS-1",
                "sourceTaskId": "TASK-1",
                "experiencePrinciples": ["small verified change"],
            }
        ],
        "negativeCases": [
            {
                "caseId": "NEG-1",
                "sourceTaskId": "TASK-2",
                "notApplicableConditions": ["margin below floor"],
            }
        ],
        "approvedCaseIds": ["POS-1", "NEG-1"],
    }
    fake_package = {
        "actionFamily": "roas_scale",
        "productIdentity": {"platform": "天猫", "verticalCategory": "服饰"},
        "ragContextSnapshot": fake_snapshot,
    }
    agent2_context = module.build_agent2_unified_knowledge_context(fake_package, fake_snapshot)
    assert agent2_context["legacyDirectKnowledgeReadAllowed"] is False
    assert len(agent2_context["compatibilitySupplement"]) == 2
    assert all(item["formalRetrievalEligible"] is False for item in agent2_context["compatibilitySupplement"])
    assert all(item["mayCreateSystemFact"] is False for item in agent2_context["compatibilitySupplement"])

    agent3_context = module.build_agent3_unified_knowledge_context(
        fake_package,
        {"actionFamily": "roas_scale"},
    )
    assert agent3_context["newAgent3SemanticRuntimeIntroduced"] is False
    assert agent3_context["runtimeRole"] == "CURRENT_DETERMINISTIC_SOP_STAGE_KNOWLEDGE_PROJECTION"

    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    service_source = service_path.read_text(encoding="utf-8")
    runtime_source = runtime_path.read_text(encoding="utf-8")
    v22_pos = bootstrap.index("install_v22_runtime()")
    v25_pos = bootstrap.index("install_v25_unified_agent_knowledge()")
    bootstrap_order = v22_pos < v25_pos

    assert bootstrap_order
    assert 'compact.pop("ragContext", None)' in service_source
    assert "agent1._experience_cards = lambda limit=16: []" in service_source
    assert "agent1_worker.build_operating_policy_context = agent1_context_v25" in service_source
    assert "action_pack.build_agent_rag_context_snapshot = agent2_snapshot_v25" in service_source
    assert "agent2._compact_package = compact_package_v25" in service_source
    assert "sop_worker.build_sop_decision_from_package = sop_builder_v25" in service_source
    assert "pipeline.run_agent_pipeline_tick =" not in service_source
    assert "pipeline.agent_pipeline_status =" not in service_source
    assert "agent1_worker._real_agent_judgments = agent1._real_agent_judgments" in runtime_source
    assert "agent2_worker.call_agent2_action_plans = agent2.call_agent2_action_plans" in runtime_source
    assert "sop_worker.build_sop_decision_from_package = sop.build_sop_decision_from_package" in runtime_source

    material = {
        "schema": "v25.phase3_agent_knowledge_migration_evidence.v1",
        "version": "25.9.0",
        "verified": True,
        "agent1KnowledgeMigrated": True,
        "agent2KnowledgeMigrated": True,
        "agent3KnowledgeMigrated": True,
        "bootstrapInstallsV25AfterV22": bootstrap_order,
        "agent1LegacyExperienceDirectReadRemoved": True,
        "agent2DirectRagContextRemoved": True,
        "agent2LegacyProviderBehindUnifiedAdapter": True,
        "agent3KnowledgeProjectedIntoCurrentSopStage": True,
        "newAgent3RuntimeIntroduced": False,
        "runtimeEntrypointsUnchanged": True,
        "physicalRagProviderCutover": False,
        "retrievalMayCreateSystemFact": False,
        "sampleAgent1CompositionHash": a1["compositionHash"],
        "sampleAgent2CompositionHash": a2["compositionHash"],
        "sampleAgent3CompositionHash": a3["compositionHash"],
        "sampleAgent2CompatibilityCount": len(agent2_context["compatibilitySupplement"]),
        "sampleAgent3GapCount": len(agent3_context["insufficientEvidence"]),
        "sourceFiles": {
            "bootstrap": "src/__init__.py",
            "runtime": "src/services/v22_runtime_service.py",
            "unifiedKnowledgeIngress": "src/services/unified_agent_knowledge_v25_service.py",
        },
    }
    evidence = {**material, "evidenceHash": canonical_hash(material)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
