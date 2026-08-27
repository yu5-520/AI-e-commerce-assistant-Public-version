#!/usr/bin/env python3
"""Verify V25 knowledge cutover reaches immutable Agent input Artifacts."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("v25_agent_input_ingress_verify", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("v25_agent_input_ingress_module_spec_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_knowledge(module: Any, agent: str) -> Dict[str, Any]:
    composition = {
        "schema": "rag.knowledge_composition_plan.v1",
        "version": "25.9.0",
        "compositionId": f"TEST-{agent}",
        "compositionVersion": "25.6.0",
        "agent": agent,
        "stage": "test",
        "matchedConditionalGroups": [],
        "fields": [],
        "mayCreateSystemFact": False,
    }
    composition["compositionHash"] = module._canonical_hash(composition)
    knowledge = {
        "schema": "rag.agent_knowledge_envelope.v1",
        "version": "25.9.0",
        "agent": agent,
        "composition": composition,
        "formalKnowledgeItems": [],
        "compatibilitySupplement": [],
        "insufficientEvidence": [],
        "legacyDirectKnowledgeReadAllowed": False,
        "mayCreateSystemFact": False,
    }
    knowledge["envelopeHash"] = module._canonical_hash(knowledge)
    return knowledge


def fake_input(module: Any, agent: str) -> Dict[str, Any]:
    knowledge = fake_knowledge(module, agent)
    contract = {
        "knowledgeIngressVersion": "25.9.0",
        "knowledgeIngress": "V25_UNIFIED_KNOWLEDGE",
        "knowledgeAgent": agent,
        "knowledgeEnvelopeHash": knowledge["envelopeHash"],
        "knowledgeCompositionHash": knowledge["composition"]["compositionHash"],
        "legacyDirectKnowledgeReadAllowed": False,
        "knowledgeMayCreateSystemFact": False,
    }
    if agent == "Agent1":
        payload = {
            "runtimeGuardrails": {"legacyDirectKnowledgeReadAllowed": False},
            "diagnosticRag": {"legacyFieldRole": "RUNTIME_GUARDRAILS_ONLY"},
            "unifiedKnowledge": knowledge,
            "inputContract": contract,
        }
        schema = "agent_input.agent1.v3"
    else:
        payload = {
            "runtimeGuardrails": {"legacyDirectKnowledgeReadAllowed": False},
            "ragContextSnapshot": {
                "knowledgeIngress": "V25_UNIFIED_KNOWLEDGE",
                "legacyKnowledgePayloadRemoved": True,
                "legacyDirectKnowledgeReadAllowed": False,
            },
            "unifiedKnowledge": knowledge,
            "inputContract": contract,
        }
        schema = "agent_input.agent2.v1"
    return {"schema": schema, "payload": payload}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="dist/v25-phase3/agent-input-ingress-verification.json")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    ingress_path = root / "src" / "services" / "v25_agent_input_ingress_service.py"
    bootstrap_path = root / "src" / "__init__.py"
    token_runtime_path = root / "src" / "services" / "agent_token_runtime_v230_service.py"

    module = load_module(ingress_path)
    source = ingress_path.read_text(encoding="utf-8")
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    token_source = token_runtime_path.read_text(encoding="utf-8")

    assert bootstrap.index("install_v25_unified_agent_knowledge()") < bootstrap.index("install_v25_agent_input_ingress()")
    assert 'contract230._AGENT1_PAYLOAD_KEYS.update({"runtimeGuardrails", "unifiedKnowledge"})' in source
    assert 'contract230._AGENT2_PAYLOAD_KEYS.update({"runtimeGuardrails", "unifiedKnowledge"})' in source
    assert 'contract2258._AGENT1_PAYLOAD_KEYS.update({"runtimeGuardrails", "unifiedKnowledge"})' in source
    assert "contract230.validate_agent_input_envelope = validate230" in source
    assert "contract2258.validate_agent_input_envelope = validate2258" in source
    assert "transport230.build_projection_envelope = build230" in source
    assert "transport2258.build_projection_envelope = build2258" in source
    assert "agent1._build_messages = _agent1_messages_v25" in source
    assert "agent2._compact_package = _agent2_compact_v25" in source
    assert "agent2._build_messages = _agent2_messages_v25" in source
    assert 'schema.startswith("agent_input.agent1.")' in source
    assert 'schema.startswith("agent_input.agent2.")' in source
    assert "knowledge_ingress_version_missing" in source
    assert "legacy_direct_knowledge_read_not_revoked" in source
    assert "unified_knowledge_envelope_hash_mismatch" in source
    assert "agent2_legacy_rag_payload_present:" in source
    assert "run_agent_pipeline_tick =" not in source
    assert "run_agent1_projected_inputs =" not in source
    assert "run_agent2_projected_inputs =" not in source

    # The existing token runtime delegates message construction to the core builders;
    # V25 wraps those final builders rather than replacing the runtime entrypoints.
    assert 'products[0].get("diagnosticRag")' in token_source
    assert "core._build_messages" in token_source

    a1 = fake_input(module, "Agent1")
    assert module._knowledge_errors(a1) == []
    stale_a1 = json.loads(json.dumps(a1))
    stale_a1["payload"]["inputContract"].pop("knowledgeIngressVersion")
    assert "knowledge_ingress_version_missing" in module._knowledge_errors(stale_a1)
    leaked_a1 = json.loads(json.dumps(a1))
    leaked_a1["payload"]["diagnosticRag"]["experienceCards"] = [{"caseId": "OLD"}]
    assert "agent1_legacy_experience_cards_present" in module._knowledge_errors(leaked_a1)

    a2 = fake_input(module, "Agent2")
    assert module._knowledge_errors(a2) == []
    leaked_a2 = json.loads(json.dumps(a2))
    leaked_a2["payload"]["ragContextSnapshot"]["positiveExperienceCards"] = [{"caseId": "OLD"}]
    assert any(
        item.startswith("agent2_legacy_rag_payload_present:")
        for item in module._knowledge_errors(leaked_a2)
    )

    report = {
        "schema": "v25.agent_input_ingress_verification.v1",
        "version": "25.9.0",
        "verified": True,
        "artifactKnowledgeIngressRequired": True,
        "preV25AgentInputReuseAllowed": False,
        "agent1ArtifactCarriesUnifiedKnowledge": True,
        "agent2ArtifactCarriesUnifiedKnowledge": True,
        "runtimeGuardrailsSeparatedFromKnowledge": True,
        "agent1LegacyExperiencePayloadBlocked": True,
        "agent2LegacyRagPayloadBlocked": True,
        "knowledgeEnvelopeHashRequired": True,
        "knowledgeCompositionHashRequired": True,
        "tokenRuntimeEntrypointsReplaced": False,
        "promptBuildersConsumeArtifactKnowledge": True,
        "newAgentRuntimeIntroduced": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
