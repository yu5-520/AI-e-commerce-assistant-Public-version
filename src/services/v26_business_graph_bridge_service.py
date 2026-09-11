"""V26.2 business-graph bridge for the active three-Agent runtime.

V26.1 established field-header authority. V26.2 migrates the live semantic surface
without replacing the proven V22/V23 execution/hash runtime:

- Agent1 -> JudgementGraph (judgement.*)
- Agent2 -> ActionGraph (plan.*)
- Agent3 -> OperationGraph (operation.* + operation_stage.*)

The bridge deliberately wraps the existing prompt/normalization seams. Historical
normalizers still enforce execution identity and legacy compatibility contracts; the
new graphs are then compiled from the exact same provider result and validated by the
V26 authority contract. SystemStage / call topology is never model writable.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from src.services.v26_field_authority_contract_service import V26FieldAuthorityContract

V26_BUSINESS_GRAPH_VERSION = "26.2.0"
JUDGEMENT_GRAPH_SCHEMA = "v26.judgement_graph.v1"
ACTION_GRAPH_SCHEMA = "v26.action_graph.v1"
OPERATION_GRAPH_SCHEMA = "v26.operation_graph.v1"

_AUTHORITY: V26FieldAuthorityContract | None = None
_INSTALLED = False


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 12000) -> str:
    if isinstance(value, dict):
        value = value.get("summary") or value.get("text") or value.get("action") or value.get("title")
    return " ".join(str(value or "").split())[:limit]


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _authority() -> V26FieldAuthorityContract:
    global _AUTHORITY
    if _AUTHORITY is None:
        _AUTHORITY = V26FieldAuthorityContract()
    return _AUTHORITY


def _clean_headers(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if item not in (None, "", [], {})
    }


def _execution_identity(value: Mapping[str, Any]) -> Dict[str, Any]:
    return _clean_headers(
        {
            "itemExecutionId": value.get("itemExecutionId"),
            "executionHash": value.get("executionHash"),
            "inputContentHash": value.get("inputContentHash"),
            "inputArtifactRef": value.get("inputArtifactRef"),
            "outputArtifactRef": value.get("outputArtifactRef"),
            "productId": value.get("productId"),
            "storeId": value.get("storeId"),
            "dataVersion": value.get("dataVersion"),
        }
    )


def _validated_graph(
    *,
    schema: str,
    actor: str,
    headers: Mapping[str, Any],
    source: Mapping[str, Any],
    compatibility_source: str,
) -> Dict[str, Any]:
    clean = _clean_headers(headers)
    _authority().assert_payload_write(actor, clean)
    body = {
        "schema": schema,
        "version": V26_BUSINESS_GRAPH_VERSION,
        "actor": actor,
        "authorityHeaders": clean,
        "sourceExecutionIdentity": _execution_identity(source),
        "compatibilitySource": compatibility_source,
        "legacyBusinessFieldsAuthoritative": False,
        "fieldHeaderAuthority": True,
    }
    return {**body, "graphHash": _canonical_hash(body)}


def _raw_by_execution(payload: Mapping[str, Any], key: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in _arr(payload.get(key)):
        if not isinstance(item, dict):
            continue
        execution_id = _text(item.get("itemExecutionId"), 180)
        if execution_id:
            result[execution_id] = item
    return result


def compile_judgement_graph(
    normalized: Mapping[str, Any],
    raw: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    item = _dict(normalized)
    raw = _dict(raw)
    judgement = _dict(item.get("agent1OperatingJudgment"))
    decision = _dict(item.get("agent1DecisionIR") or judgement.get("agent1DecisionIR"))
    reasoning = _text(
        raw.get("decisionSummary")
        or decision.get("decisionSummary")
        or item.get("finding")
        or judgement.get("businessHypothesis")
    )
    primary_issue = _text(
        raw.get("coreProblem")
        or decision.get("coreProblem")
        or judgement.get("primaryOperatingGap")
        or item.get("finding")
    )
    action_family = _text(
        raw.get("selectedActionFamilyHint")
        or decision.get("selectedActionFamily")
        or item.get("selectedActionFamilyHint")
        or judgement.get("selectedActionFamily")
    )
    recommended_direction = _text(
        raw.get("recommendedDirection")
        or raw.get("actionIntent")
        or decision.get("actionIntent")
        or item.get("selectedOperatingRoute")
    )
    confidence = raw.get("confidence")
    if confidence in (None, ""):
        confidence = item.get("confidence")
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except Exception:
        confidence_value = None
    priority = raw.get("priorityWeight")
    try:
        priority_value = max(0.0, min(1.0, float(priority))) if priority not in (None, "") else None
    except Exception:
        priority_value = None
    refs: List[str] = []
    for ref in _dict(item.get("artifactRefs")).values():
        if isinstance(ref, str) and ref:
            refs.append(ref)
    rag_proof = _dict(decision.get("ragProof") or raw.get("ragProof"))
    for ref in rag_proof.values():
        if isinstance(ref, str) and ref:
            refs.append(ref)
    headers = {
        "judgement.reasoning": reasoning,
        "judgement.primary_issue": primary_issue,
        "judgement.secondary_signals": raw.get("secondarySignals") or decision.get("facts") or judgement.get("evidenceFacts"),
        "judgement.signal_conflicts": raw.get("signalConflicts"),
        "judgement.possible_causes": raw.get("possibleCauses") or decision.get("causalHypotheses"),
        "judgement.rejected_hypotheses": raw.get("rejectedHypotheses") or decision.get("rejectedHypotheses"),
        "judgement.evidence_refs": list(dict.fromkeys(refs)),
        "judgement.ignored_signals": raw.get("ignoredSignals"),
        "judgement.recommended_direction": recommended_direction,
        "judgement.action_family": action_family,
        "judgement.priority_weight": priority_value,
        "judgement.evidence_confidence": confidence_value,
    }
    return _validated_graph(
        schema=JUDGEMENT_GRAPH_SCHEMA,
        actor="agent1",
        headers=headers,
        source=item,
        compatibility_source="agent1OperatingJudgment+agent1DecisionIR",
    )


def _first_number(sources: Iterable[Mapping[str, Any]], *keys: str) -> float | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except Exception:
                continue
    return None


def _first_value(sources: Iterable[Mapping[str, Any]], *keys: str) -> Any:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, "", [], {}):
                return value
    return None


def compile_action_graph(
    normalized: Mapping[str, Any],
    raw: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    draft = _dict(normalized)
    raw = _dict(raw)
    raw_payload = _dict(raw.get("familyPayload"))
    payload = _dict(draft.get("familyPayload"))
    sources = [raw_payload, payload, raw, draft]
    strategy_summary = _text(
        _first_value(
            sources,
            "strategySummary",
            "strategyReason",
            "differentiationReason",
            "repairDetail",
            "experimentDirection",
        )
        or draft.get("differentiationReason")
        or draft.get("actionIntent")
    )
    candidates = _first_value(
        sources,
        "candidateStrategies",
        "directions",
        "operations",
        "alternatives",
    )
    selected_strategy = _first_value(
        sources,
        "selectedStrategy",
        "operationPlan",
    ) or payload
    review_window = _first_value(sources, "reviewWindow", "review_window", "verificationPeriod")
    headers = {
        "plan.strategy_summary": strategy_summary,
        "plan.candidate_strategies": candidates,
        "plan.selected_strategy": selected_strategy,
        "plan.parameter_pack": payload,
        "plan.daily_budget": _first_number(sources, "dailyBudget", "daily_budget", "budget"),
        "plan.target_roas": _first_number(sources, "targetRoas", "targetROAS", "target_roas"),
        "plan.review_window": _text(review_window, 240) if review_window not in (None, "") else None,
        "plan.expected_trend": _first_value(sources, "expectedTrend", "expected_trend"),
        "plan.lower_guard": _first_value(sources, "lowerGuard", "lower_guard", "lowerBoundary"),
        "plan.upper_guard": _first_value(sources, "upperGuard", "upper_guard", "upperBoundary"),
        "plan.minimum_evidence": _first_value(sources, "minimumEvidence", "minimum_evidence", "requiredEvidence"),
        "plan.acceptance_criteria": _first_value(sources, "acceptanceCriteria", "validationMetrics") or draft.get("validationMetrics"),
        "plan.risk_boundaries": _first_value(sources, "riskBoundaries", "stopConditions") or draft.get("riskBoundaries"),
    }
    return _validated_graph(
        schema=ACTION_GRAPH_SCHEMA,
        actor="agent2",
        headers=headers,
        source=draft,
        compatibility_source="agent2ActionDraft+familyPayload",
    )


def _operation_stage_headers(stage: Mapping[str, Any], index: int) -> Dict[str, Any]:
    stage = _dict(stage)
    return _clean_headers(
        {
            "operation_stage.stage_id": _text(stage.get("stageId") or stage.get("id") or f"STAGE-{index + 1}", 160),
            "operation_stage.stage_name": _text(stage.get("stageName") or stage.get("name") or stage.get("title") or f"Stage {index + 1}", 500),
            "operation_stage.objective": _text(stage.get("objective"), 2000),
            "operation_stage.dependencies": stage.get("dependencies"),
            "operation_stage.required_inputs": stage.get("requiredInputs") or stage.get("inputs"),
            "operation_stage.steps": stage.get("steps") or stage.get("executionSteps"),
            "operation_stage.tool_capabilities": stage.get("toolCapabilities") or stage.get("capabilities"),
            "operation_stage.artifacts": stage.get("artifacts") or stage.get("outputs"),
            "operation_stage.acceptance_criteria": stage.get("acceptanceCriteria") or stage.get("completionCriteria"),
            "operation_stage.rollback": stage.get("rollback") or stage.get("rollbackConditions"),
            "operation_stage.affected_fields": stage.get("affectedFields"),
            "operation_stage.affected_metrics": stage.get("affectedMetrics") or stage.get("reviewMetrics"),
            "operation_stage.review_entry": _text(stage.get("reviewEntry") or stage.get("reviewReason"), 1200),
        }
    )


def _compat_operation_stages(sop: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic bridge only for historical outputs lacking provider stages.

    V26.2 prompts request provider-authored operationStages. Old exact replays can still
    appear during migration; each already-authoritative execution step is projected as
    one operation stage rather than being discarded or re-generated by another LLM.
    """
    result: List[Dict[str, Any]] = []
    for index, step in enumerate(_arr(sop.get("executionSteps"))):
        if not isinstance(step, dict):
            continue
        result.append(
            {
                "stageId": step.get("stageId") or step.get("stepId") or f"STAGE-{index + 1}",
                "stageName": step.get("stageName") or step.get("actionType") or f"执行阶段 {index + 1}",
                "objective": step.get("instruction"),
                "steps": [step],
                "acceptanceCriteria": step.get("completionCriteria"),
                "affectedFields": step.get("affectedFields"),
                "affectedMetrics": step.get("affectedMetrics"),
            }
        )
    return result


def compile_operation_graph(
    normalized: Mapping[str, Any],
    raw: Mapping[str, Any] | None = None,
    package: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    sop = _dict(normalized)
    raw = _dict(raw)
    package = _dict(package)
    draft = _dict(package.get("agent2ActionDraft"))
    action_graph = _dict(draft.get("v26ActionGraph"))
    plan_headers = _dict(action_graph.get("authorityHeaders"))
    plan_refs = sorted(key for key in plan_headers if key.startswith("plan."))
    stages = [item for item in _arr(raw.get("operationStages")) if isinstance(item, dict)]
    compatibility_projection = False
    if not stages:
        stages = _compat_operation_stages(sop)
        compatibility_projection = True
    stage_graphs: List[Dict[str, Any]] = []
    for index, stage in enumerate(stages):
        headers = _operation_stage_headers(stage, index)
        _authority().assert_stage_write("agent3", "operationStage")
        _authority().assert_payload_write("agent3", headers)
        body = {
            "schema": "v26.operation_stage.v1",
            "version": V26_BUSINESS_GRAPH_VERSION,
            "authorityHeaders": headers,
            "index": index,
        }
        stage_graphs.append({**body, "stageHash": _canonical_hash(body)})
    headers = {
        "operation.objective": _text(raw.get("executionObjective") or sop.get("executionObjective"), 2000),
        "operation.summary": _text(raw.get("finalTaskTitle") or sop.get("finalTaskTitle"), 1000),
        "operation.plan_refs": plan_refs,
        "operation.review_reason": _text(raw.get("reviewReason") or sop.get("companyStyleReason"), 1200),
        "operation.step_instruction": "\n".join(
            _text(step.get("instruction"), 1600)
            for step in _arr(sop.get("executionSteps"))
            if isinstance(step, dict) and _text(step.get("instruction"), 1600)
        )[:12000],
    }
    graph = _validated_graph(
        schema=OPERATION_GRAPH_SCHEMA,
        actor="agent3",
        headers=headers,
        source=sop,
        compatibility_source="agent3Sop+operationStages",
    )
    graph["operationStages"] = stage_graphs
    graph["operationStageCount"] = len(stage_graphs)
    graph["compatibilityStageProjection"] = compatibility_projection
    graph["systemStageMutationAllowed"] = False
    graph["graphHash"] = _canonical_hash({key: value for key, value in graph.items() if key != "graphHash"})
    return graph


def _append_system_contract(messages: List[Dict[str, str]], addition: str) -> List[Dict[str, str]]:
    result = [dict(item) for item in messages]
    for item in result:
        if item.get("role") == "system":
            item["content"] = str(item.get("content") or "") + "\n" + addition
            return result
    return [{"role": "system", "content": addition}, *result]


def install_v26_business_graph_bridge() -> Dict[str, Any]:
    """Install V26.2 at existing prompt/normalization seams, exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return {
            "version": V26_BUSINESS_GRAPH_VERSION,
            "installed": True,
            "idempotentReplay": True,
        }

    from src.services import agent2_action_draft_core_v225_service as agent2_core
    from src.services import agent_token_runtime_v22520_service as agent2_runtime
    from src.services import agent3_sop_core_v225_service as agent3_core
    from src.services import real_product_judgment_agent_v2259_service as agent1_core

    agent1_build = agent1_core._build_messages
    agent1_normalize = agent1_core._normalize_judgments

    def agent1_build_v26(*args, **kwargs):
        messages, payload = agent1_build(*args, **kwargs)
        return _append_system_contract(
            messages,
            "V26.2 JudgementGraph活跃面：在原合同字段之外，可以返回signalConflicts、ignoredSignals、"
            "recommendedDirection、priorityWeight；业务自然语言不做关键词模板限制。priorityWeight如返回必须0到1。"
            "不得改变itemExecutionId/inputContentHash、事实来源、SystemStage或权限边界。",
        ), payload

    def agent1_normalize_v26(provider_payload, products, data_version):
        normalized, diagnostics = agent1_normalize(provider_payload, products, data_version)
        raw_map = _raw_by_execution(_dict(provider_payload), "judgments")
        for item in normalized:
            execution_id = _text(_dict(item).get("itemExecutionId"), 180)
            item["v26JudgementGraph"] = compile_judgement_graph(item, raw_map.get(execution_id))
        return normalized, diagnostics

    agent1_core._build_messages = agent1_build_v26
    agent1_core._normalize_judgments = agent1_normalize_v26

    agent2_build = agent2_core._build_messages
    agent2_normalize = agent2_core._normalize_draft

    def agent2_build_v26(*args, **kwargs):
        messages, payload = agent2_build(*args, **kwargs)
        return _append_system_contract(
            messages,
            "V26.2 ActionGraph活跃面：familyPayload在保持原动作族必填字段的同时，可以增加strategySummary、"
            "candidateStrategies、selectedStrategy、expectedTrend、lowerGuard、upperGuard、minimumEvidence、"
            "acceptanceCriteria。你仍不得改写Agent1锁定的主问题/动作族/执行对象/权限，也不得伪造事实。",
        ), payload

    def agent2_normalize_v26(raw, package, proof=None):
        normalized = agent2_normalize(raw, package, proof)
        normalized["v26ActionGraph"] = compile_action_graph(normalized, raw)
        return normalized

    agent2_core._build_messages = agent2_build_v26
    agent2_core._normalize_draft = agent2_normalize_v26
    # V22.5.20 imported these functions by value before this bridge is installed.
    # Rebind the active module globals explicitly so live calls use the V26.2 seam.
    agent2_runtime._build_messages = agent2_build_v26
    agent2_runtime._normalize_draft = agent2_normalize_v26

    agent3_build = agent3_core._build_messages
    agent3_normalize = agent3_core._normalize_sop

    def agent3_build_v26(*args, **kwargs):
        messages, payload = agent3_build(*args, **kwargs)
        return _append_system_contract(
            messages,
            "V26.2 OperationGraph活跃面：在保持现有executionSteps/stop/rollback合同有效的同时，必须额外输出"
            "operationStages数组。阶段数量按业务需要动态决定，不固定模板。每个阶段可包含stageId,stageName,objective,"
            "dependencies,requiredInputs,steps,toolCapabilities,artifacts,acceptanceCriteria,rollback,affectedFields,"
            "affectedMetrics,reviewEntry。operationStages只能描述业务执行阶段，绝不能创建Agent、SystemStage或调用边。"
            "所有预算/ROAS等Agent2计划数值只能引用，不得在Agent3重新制定。",
        ), payload

    def agent3_normalize_v26(raw, package, proof=None):
        normalized = agent3_normalize(raw, package, proof)
        normalized["v26OperationGraph"] = compile_operation_graph(normalized, raw, package)
        return normalized

    agent3_core._build_messages = agent3_build_v26
    agent3_core._normalize_sop = agent3_normalize_v26

    _INSTALLED = True
    return {
        "version": V26_BUSINESS_GRAPH_VERSION,
        "installed": True,
        "judgementGraph": JUDGEMENT_GRAPH_SCHEMA,
        "actionGraph": ACTION_GRAPH_SCHEMA,
        "operationGraph": OPERATION_GRAPH_SCHEMA,
        "legacyBusinessFieldsAuthoritative": False,
        "systemStageMutationAllowed": False,
        "operationStageDynamic": True,
        "agent3PlanNumberMode": "reference_only",
    }


__all__ = [
    "V26_BUSINESS_GRAPH_VERSION",
    "JUDGEMENT_GRAPH_SCHEMA",
    "ACTION_GRAPH_SCHEMA",
    "OPERATION_GRAPH_SCHEMA",
    "compile_judgement_graph",
    "compile_action_graph",
    "compile_operation_graph",
    "install_v26_business_graph_bridge",
]
