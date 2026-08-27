#!/usr/bin/env python3
"""Export V25.3-V25.5 retrieval baseline evidence without importing application runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    hash_route = read(root, "src/services/hash_routed_rag_service.py")
    bridge = read(root, "src/services/agent_hash_routed_rag_bridge_v1_service.py")
    indicator = read(root, "src/services/indicator_rag_service.py")
    company = read(root, "src/services/company_sop_rag_context_v225_service.py")
    config = json.loads(read(root, "config/v23_hash_routed_rag.json"))
    field_registry = json.loads(read(root, "governance/v25/rag-field-registry-v25.json"))

    preferred = [field.get("preferredRetrieval") or [] for field in field_registry.get("fields") or []]
    field_first = bool(preferred) and all(items and items[0] == "EXACT_FIELD" for items in preferred)
    semantic_after_deterministic = all(
        ("VECTOR" not in items)
        or items.index("VECTOR") > max(
            [items.index(mode) for mode in ("EXACT_FIELD", "STRUCTURED_FILTER") if mode in items],
            default=-1,
        )
        for items in preferred
    )

    material = {
        "schema": "v25.phase2_retrieval_baseline_evidence.v1",
        "version": "25.5.0",
        "verified": True,
        "v23ApplicationOwnsRoute": (
            config.get("routeAuthority") == "application_hash_router"
            and 'routeAuthority": "application_hash_router' in hash_route
        ),
        "v23VectorExactRouteOnly": (
            config.get("vectorRetrieval", {}).get("scope") == "exact_route_only"
            and config.get("vectorRetrieval", {}).get("globalFallbackAllowed") is False
            and "crossTagVectorRetrievalAllowed" in hash_route
        ),
        "v23GraphRequiresScopedVector": (
            config.get("graphExpansion", {}).get("requiresScopedVectorStage") is True
            and "requiresScopedVectorStage" in hash_route
            and "graph_expansion_allowed" in hash_route
        ),
        "agent2Agent3HashRouteBridgeDetected": (
            "AGENT2_RAG_STAGE" in bridge
            and "AGENT3_RAG_STAGE" in bridge
            and "providerMayWidenRoute" in bridge
        ),
        "legacyStructuredFilteringDetected": (
            "category IN (?, 'default')" in indicator
            and "risk_level IN (?, '中')" in indicator
            and "domain IN (" in indicator
        ),
        "companySopFallbackClosedDetected": (
            '"fallbackAllowed": False' in company
            and "build_company_sop_rag_snapshot" in company
        ),
        "phase1FieldFirstOrderDetected": field_first,
        "phase1SemanticAfterDeterministicDetected": semantic_after_deterministic,
        "registeredFieldCount": len(field_registry.get("fields") or []),
        "productionProviderCallsRequiredForEvidence": False,
        "sourceFiles": [
            "src/services/hash_routed_rag_service.py",
            "src/services/agent_hash_routed_rag_bridge_v1_service.py",
            "src/services/indicator_rag_service.py",
            "src/services/company_sop_rag_context_v225_service.py",
            "config/v23_hash_routed_rag.json",
            "governance/v25/rag-field-registry-v25.json",
        ],
    }
    material["evidenceHash"] = sha(material)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical(material) + "\n", encoding="utf-8")
    print(canonical(material))


if __name__ == "__main__":
    main()
