"""LLM enrichment for Module / Task / Feedback Agents.

V4.5.3 keeps problemType and ActionPlan deterministic, then uses the LLM Gateway
to enrich operator brief, manager review brief, risk checks, and experience-card
wording. RAG retrieval remains structured and review-gated through
experience_memory_service.search_cases.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List

from src.services.experience_memory_service import search_cases
from src.services.llm_provider_service import generate_json

AGENT_LLM_ENRICHMENT_VERSION = "4.5.3"
ACTION_EXPECTED_KEYS = ["llmSummary", "operatorBrief", "managerReviewBrief", "riskCheck"]
FEEDBACK_EXPECTED_KEYS = ["llmSummary", "experienceCardDraft", "riskCheck"]


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _compact_text(values: Iterable[Any], limit: int = 800) -> str:
    text = " ".join(str(value) for value in values if value)
    return text[:limit]


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _snapshot(result: Dict[str, Any]) -> Dict[str, Any]:
    return deepcopy(
        _first_nonempty(
            result.get("sourceSnapshot"),
            result.get("inputSnapshot"),
            result.get("productFacts"),
            result.get("task"),
            result.get("experienceCard"),
            {},
        )
    )


def _problem_type(result: Dict[str, Any]) -> str | None:
    return _first_nonempty(
        result.get("problemType"),
        (result.get("actionPlan") or {}).get("problemType"),
        (result.get("taskDraft") or {}).get("problemType"),
        (result.get("experienceCard") or {}).get("problemType"),
    )


def _rag_filters(result: Dict[str, Any]) -> Dict[str, Any]:
    source = _snapshot(result)
    task_draft = result.get("taskDraft") or {}
    action_plan = result.get("actionPlan") or task_draft.get("actionPlan") or {}
    return {
        "category_id": _first_nonempty(result.get("categoryId"), task_draft.get("categoryId"), source.get("categoryId")),
        "platform": _first_nonempty(result.get("platform"), task_draft.get("platform"), source.get("platform")),
        "store_id": _first_nonempty(result.get("storeId"), task_draft.get("storeId"), source.get("storeId"), "global"),
        "problem_type": _first_nonempty(result.get("problemType"), action_plan.get("problemType"), source.get("problemType")),
    }


def _rag_query(result: Dict[str, Any]) -> str:
    action_plan = result.get("actionPlan") or {}
    package = action_plan.get("recommendedPackage") or result.get("recommendedPackage") or result.get("selectedPackage") or {}
    task_draft = result.get("taskDraft") or {}
    return _compact_text(
        [
            result.get("summary"),
            result.get("task"),
            result.get("reason"),
            result.get("problemLabel"),
            action_plan.get("problemLabel"),
            action_plan.get("diagnosis"),
            package.get("packageName"),
            task_draft.get("task"),
            task_draft.get("reason"),
            *(_as_list(result.get("ruleHits"))),
            *(_as_list(result.get("suggestions"))),
            *(_as_list(result.get("humanDecision"))),
            *(_as_list(task_draft.get("judgmentTags"))),
        ]
    )


def retrieve_rag_cases_for_result(result: Dict[str, Any], *, limit: int = 5) -> List[Dict[str, Any]]:
    filters = _rag_filters(result)
    rag = search_cases(
        query=_rag_query(result),
        category_id=filters.get("category_id"),
        platform=filters.get("platform"),
        store_id=filters.get("store_id") or "global",
        problem_type=filters.get("problem_type"),
        effective_only=False,
        min_quality=0.0,
        limit=limit,
    )
    return rag.get("items") or []


def _action_payload(result: Dict[str, Any], rag_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "problemType": _problem_type(result),
        "problemLabel": result.get("problemLabel") or (result.get("actionPlan") or {}).get("problemLabel"),
        "actionPlan": result.get("actionPlan") or {},
        "executionPackages": result.get("executionPackages") or (result.get("actionPlan") or {}).get("executionPackages") or [],
        "taskDraft": result.get("taskDraft") or {},
        "taskDrafts": result.get("taskDrafts") or [],
        "strategies": result.get("strategies") or [],
        "sourceSnapshot": _snapshot(result),
        "evidence": result.get("evidence") or [],
        "ragReferences": [case.get("caseId") for case in rag_items],
        "retrievedCases": rag_items,
        "forbiddenActions": result.get("forbiddenActions") or [],
        "boundary": result.get("boundary"),
    }


def _apply_action_output(result: Dict[str, Any], llm: Dict[str, Any], rag_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    output = llm.get("output") or {}
    result["retrievedCases"] = rag_items
    result["ragReferences"] = list(dict.fromkeys([*(_as_list(result.get("ragReferences"))), *[case.get("caseId") for case in rag_items if case.get("caseId")]]))
    result["llmEnrichment"] = llm
    result["llmSummary"] = output.get("llmSummary")
    result["llmOperatorBrief"] = output.get("operatorBrief")
    result["llmManagerReviewBrief"] = output.get("managerReviewBrief")
    result["llmRiskCheck"] = output.get("riskCheck") or []
    result["llmFallbackUsed"] = bool(llm.get("fallbackUsed"))
    result["llmProvider"] = llm.get("provider")
    result["llmModel"] = llm.get("model")
    result["agentLlmEnrichmentVersion"] = AGENT_LLM_ENRICHMENT_VERSION
    for draft in result.get("taskDrafts") or []:
        draft["retrievedCases"] = rag_items
        draft["ragReferences"] = result["ragReferences"]
        draft["llmSummary"] = result.get("llmSummary")
        draft["llmOperatorBrief"] = result.get("llmOperatorBrief")
        draft["llmManagerReviewBrief"] = result.get("llmManagerReviewBrief")
        draft["llmRiskCheck"] = result.get("llmRiskCheck") or []
        draft["llmFallbackUsed"] = result.get("llmFallbackUsed")
    if result.get("taskDraft"):
        result["taskDraft"]["llmSummary"] = result.get("llmSummary")
        result["taskDraft"]["llmOperatorBrief"] = result.get("llmOperatorBrief")
        result["taskDraft"]["llmManagerReviewBrief"] = result.get("llmManagerReviewBrief")
        result["taskDraft"]["llmRiskCheck"] = result.get("llmRiskCheck") or []
    return result


def enrich_action_plan_agent_result(result: Dict[str, Any], *, agent_name: str | None = None, prompt_name: str = "task_action_plan_enrich") -> Dict[str, Any]:
    enriched = deepcopy(result or {})
    rag_items = enriched.get("retrievedCases") or retrieve_rag_cases_for_result(enriched)
    llm = generate_json(
        prompt_name=prompt_name,
        payload=_action_payload(enriched, rag_items),
        expected_keys=ACTION_EXPECTED_KEYS,
        agent_name=agent_name or enriched.get("agentName") or "ActionPlan Agent",
        schema_name="task_action_plan",
    )
    return _apply_action_output(enriched, llm, rag_items)


def enrich_module_agent_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return enrich_action_plan_agent_result(result, agent_name=result.get("agentName") or "模块 Agent", prompt_name="module_report_summary")


def enrich_task_generation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    enriched = deepcopy(result or {})
    candidates = []
    for candidate in enriched.get("candidates") or []:
        item = deepcopy(candidate)
        draft = item.get("taskDraft") or {}
        seed = {
            **item,
            "taskDraft": draft,
            "actionPlan": draft.get("actionPlan") or item.get("actionPlan") or {},
            "executionPackages": item.get("executionPackages") or draft.get("executionPackages") or [],
            "retrievedCases": item.get("retrievedCases") or [],
            "sourceSnapshot": enriched.get("sourceSnapshot") or {},
            "forbiddenActions": enriched.get("forbiddenActions") or item.get("forbiddenActions") or [],
        }
        item = enrich_action_plan_agent_result(seed, agent_name="自动解析生成任务 Agent", prompt_name="task_action_plan_enrich")
        item["candidateId"] = candidate.get("candidateId")
        item["confidence"] = candidate.get("confidence")
        item["confidenceLevel"] = candidate.get("confidenceLevel")
        candidates.append(item)
    enriched["candidates"] = candidates
    enriched["agentLlmEnrichmentVersion"] = AGENT_LLM_ENRICHMENT_VERSION
    return enriched


def enrich_task_playbook_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return enrich_action_plan_agent_result(result, agent_name=result.get("agentName") or "任务解析运营方式 Agent", prompt_name="task_action_plan_enrich")


def enrich_feedback_draft_result(result: Dict[str, Any]) -> Dict[str, Any]:
    enriched = deepcopy(result or {})
    drafts = []
    for draft in enriched.get("drafts") or []:
        item = deepcopy(draft)
        card = item.get("experienceCard") or {}
        payload = {
            "experienceCard": card,
            "operatorSubmission": "；".join(card.get("effectiveActions") or []),
            "managerReview": card.get("resultSummary"),
            "beforeMetrics": card.get("beforeMetrics") or {},
            "afterMetrics": card.get("afterMetrics") or {},
            "ragReferences": [],
            "forbiddenActions": ["不自动批准经验入库", "不把原始日志直接写入正式 RAG"],
        }
        llm = generate_json(
            prompt_name="feedback_experience_card",
            payload=payload,
            expected_keys=FEEDBACK_EXPECTED_KEYS,
            agent_name="回流任务 Agent",
            schema_name="experience_card",
        )
        output = llm.get("output") or {}
        item["llmEnrichment"] = llm
        item["llmSummary"] = output.get("llmSummary")
        item["llmExperienceCardDraft"] = output.get("experienceCardDraft") or {}
        item["llmRiskCheck"] = output.get("riskCheck") or []
        item["llmFallbackUsed"] = bool(llm.get("fallbackUsed"))
        drafts.append(item)
    enriched["drafts"] = drafts
    enriched["agentLlmEnrichmentVersion"] = AGENT_LLM_ENRICHMENT_VERSION
    return enriched


def enrich_feedback_summary_result(result: Dict[str, Any]) -> Dict[str, Any]:
    enriched = deepcopy(result or {})
    candidates = enriched.get("learningCandidates") or []
    if not candidates:
        enriched["agentLlmEnrichmentVersion"] = AGENT_LLM_ENRICHMENT_VERSION
        return enriched
    sample = candidates[0]
    payload = {
        "learningCandidates": candidates[:5],
        "memorySummary": enriched.get("memorySummary") or {},
        "agentEvalMetrics": enriched.get("agentEvalMetrics") or {},
        "operatorSubmission": sample.get("operatorSubmission") or "",
        "managerReview": sample.get("managerReview") or "",
        "forbiddenActions": enriched.get("forbiddenActions") or [],
    }
    llm = generate_json(
        prompt_name="feedback_experience_card",
        payload=payload,
        expected_keys=FEEDBACK_EXPECTED_KEYS,
        agent_name=enriched.get("agentName") or "回流任务 Agent",
        schema_name="experience_card",
    )
    output = llm.get("output") or {}
    enriched["llmEnrichment"] = llm
    enriched["llmSummary"] = output.get("llmSummary")
    enriched["llmExperienceCardDraft"] = output.get("experienceCardDraft") or {}
    enriched["llmRiskCheck"] = output.get("riskCheck") or []
    enriched["llmFallbackUsed"] = bool(llm.get("fallbackUsed"))
    enriched["agentLlmEnrichmentVersion"] = AGENT_LLM_ENRICHMENT_VERSION
    return enriched
