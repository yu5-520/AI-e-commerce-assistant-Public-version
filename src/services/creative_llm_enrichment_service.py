"""Creative Agent LLM enrichment.

This service keeps V4.4.1 creative test packages deterministic, then asks the
LLM Gateway to enrich wording. If LLM is disabled or fails, the gateway returns a
mock/fallback result and the original packages remain valid.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.services.llm_provider_service import generate_json

CREATIVE_LLM_VERSION = "4.5.0"

EXPECTED_CREATIVE_KEYS = ["llmSummary", "titleVariants", "mainImageDirections", "riskCheck"]


def enrich_creative_agent_result(agent_result: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(agent_result or {})
    payload = {
        "productId": result.get("productId"),
        "taskGoal": result.get("taskGoal"),
        "productFacts": result.get("productFacts"),
        "categoryProfile": result.get("categoryProfile"),
        "platform": (result.get("productFacts") or {}).get("platform") or "通用",
        "platformRule": result.get("platformRule"),
        "competitorSignals": result.get("competitorSignals") or [],
        "ragReferences": result.get("ragReferences") or [],
        "historicalCreativePatterns": result.get("historicalCreativePatterns") or [],
        "sellingPointOrder": result.get("sellingPointOrder") or [],
        "testPackages": result.get("testPackages") or [],
        "forbiddenActions": result.get("forbiddenActions") or [],
    }
    llm = generate_json(
        prompt_name="creative_test_package",
        payload=payload,
        expected_keys=EXPECTED_CREATIVE_KEYS,
        agent_name="标题主图垂直类目 Agent",
        schema_name="creative_test_package",
    )
    output = llm.get("output") or {}
    result["llmEnrichment"] = llm
    result["llmSummary"] = output.get("llmSummary")
    result["llmTitleVariants"] = output.get("titleVariants") or []
    result["llmMainImageDirections"] = output.get("mainImageDirections") or []
    result["llmRiskCheck"] = output.get("riskCheck") or []
    result["llmBoundary"] = output.get("llmBoundary") or llm.get("boundary")
    result["llmFallbackUsed"] = bool(llm.get("fallbackUsed"))
    result["llmProvider"] = llm.get("provider")
    result["llmModel"] = llm.get("model")

    # Build display-only enriched package previews without changing the
    # deterministic task package contract.
    previews: List[Dict[str, Any]] = []
    titles = result.get("llmTitleVariants") or []
    images = result.get("llmMainImageDirections") or []
    for index, package in enumerate(result.get("testPackages") or []):
        preview = deepcopy(package)
        if index < len(titles) and titles[index].get("title"):
            preview["llmTitle"] = titles[index]["title"]
            preview["llmTitleAngle"] = titles[index].get("angle")
        if index < len(images):
            preview["llmMainImageDirection"] = images[index].get("direction")
            preview["llmFirstImageText"] = images[index].get("firstImageText")
            preview["llmMainImageLayout"] = images[index].get("layout")
        previews.append(preview)
    result["llmPackagePreviews"] = previews
    result["creativeLlmVersion"] = CREATIVE_LLM_VERSION
    return result
