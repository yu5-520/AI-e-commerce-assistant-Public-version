#!/usr/bin/env python3
"""Export V25.0-V25.2 knowledge baseline evidence without importing application runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

MARKERS = {
    "src-operating-policy": [
        "stable_operating_policy_not_dynamic_rag",
        "\"familyGuidance\"",
        "\"permissionBoundary\"",
        "\"ragBoundary\"",
    ],
    "src-dynamic-experience-rag": [
        "rag_experience_cards",
        "status = 'approved'",
        "effective = 1",
        "source_task_id",
    ],
    "src-company-sop-context": [
        "company_sop_rag",
        "managementStyle",
        "companyExecutionPrinciples",
        "brandStyleSnapshot",
    ],
    "src-agent2-family-contract": [
        "FAMILY_DRAFT_KEYS",
        "_family_contract",
        "unsupported_locked_action_family",
    ],
    "src-agent3-family-policy": [
        "_FAMILY_POLICIES",
        "allowedActionTypes",
        "forbiddenActions",
    ],
    "src-hash-routed-rag": [
        "tag_hash_first",
        "exact_route_only",
        "globalFallbackAllowed",
        "failClosed",
    ],
    "src-agent-rag-bridge": [
        "AGENT2_RAG_STAGE",
        "AGENT3_RAG_STAGE",
        "providerMayWidenRoute",
    ],
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="governance/v25/knowledge-baseline-v25.json")
    parser.add_argument("--output", default="dist/v25-phase1/knowledge-baseline-evidence.json")
    args = parser.parse_args()

    baseline_path = ROOT / args.baseline
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    sources = baseline.get("inventorySources") or []
    evidence_sources = []

    for source in sources:
        source_id = str(source.get("sourceId") or "")
        rel = str(source.get("path") or "")
        path = ROOT / rel
        if not path.is_file():
            raise SystemExit(f"knowledge_source_missing:{rel}")
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in MARKERS.get(source_id, []) if marker not in text]
        if missing:
            raise SystemExit(f"knowledge_source_marker_missing:{source_id}:{missing}")
        evidence_sources.append(
            {
                "sourceId": source_id,
                "path": rel,
                "classification": source.get("classification"),
                "sourceHash": file_hash(path),
                "markerCount": len(MARKERS.get(source_id, [])),
            }
        )

    material = {
        "schema": "v25.knowledge_baseline_evidence.v1",
        "version": "25.2.0-phase1",
        "verified": True,
        "inventorySourceCount": len(evidence_sources),
        "sources": evidence_sources,
        "agent1StaticKnowledgeInjectionDetected": any(
            item["sourceId"] == "src-operating-policy" for item in evidence_sources
        ),
        "dynamicExperienceCardsDetected": any(
            item["sourceId"] == "src-dynamic-experience-rag" for item in evidence_sources
        ),
        "companyContextStaticDefaultsDetected": any(
            item["sourceId"] == "src-company-sop-context" for item in evidence_sources
        ),
        "hashRouteContractDetected": any(
            item["sourceId"] == "src-hash-routed-rag" for item in evidence_sources
        ),
        "providerBoundaryRagBridgeDetected": any(
            item["sourceId"] == "src-agent-rag-bridge" for item in evidence_sources
        ),
        "existingUnifiedRagFieldRegistryDetected": False,
        "existingUnifiedKnowledgeStoreDetected": False,
        "productionAgentInputsChangedByExporter": False,
        "productionRagWriterChangedByExporter": False,
        "baselineHash": canonical_hash(baseline),
    }
    output = dict(material)
    output["evidenceHash"] = canonical_hash(material)
    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(canonical(output) + "\n", encoding="utf-8")
    print(canonical(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
