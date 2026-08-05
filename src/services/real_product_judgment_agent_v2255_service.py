"""V22.5.5 Agent1: rich diagnosis plus one stable execution handoff.

The legacy core remains responsible for fact cards, identity matching and diagnostic
normalization. This adapter upgrades the provider contract and fail-closes every act
result that lacks one evidence-backed primary problem, action, owner and target.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from src.runtime_version import THREE_AGENT_PIPELINE_VERSION
from src.services import real_product_judgment_agent_v196_service as legacy
from src.services.agent_execution_lock_v2255_service import (
    EVIDENCE_CONFLICT,
    EVIDENCE_INSUFFICIENT,
    EVIDENCE_SUFFICIENT,
    execution_lock_from,
    missing_execution_lock,
)

REAL_PRODUCT_AGENT_V2255_VERSION = THREE_AGENT_PIPELINE_VERSION
PRODUCT_AGENT_MODE = "v22_5_5_diagnosis_then_evidence_backed_execution_lock"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 800) -> str:
    if isinstance(value, dict):
        value = value.get("summary") or value.get("text") or value.get("action") or value.get("id")
    return " ".join(str(value or "").split())[:limit]


def build_agent1_rag_context() -> Dict[str, Any]:
    context = legacy.build_agent1_rag_context()
    principles = list(context.get("principles") or [])
    principles.extend(
        [
            "act不等于发现异常；act必须形成一个证据充分的主问题节点、主动作、直接责任人和执行对象。",
            "原因尚未确认、缺少关键证据或存在多个并列主方向时，必须observe并标记diagnostic_hold。",
            "完整因果假设用于审计；下游只接收唯一executionLock。",
            "跨部门内容只能成为supportingCoordination，不能与运营主动作并列。",
            "原生observe是合法终态，不得因为没有executionLock而误记为失败或诊断卡死。",
        ]
    )
    context["version"] = THREE_AGENT_PIPELINE_VERSION
    context["mode"] = PRODUCT_AGENT_MODE
    context["principles"] = list(dict.fromkeys(principles))
    context["guardrails"] = {
        **_dict(context.get("guardrails")),
        "actRequiresEvidenceSufficient": True,
        "onePrimaryProblemNode": True,
        "onePrimaryAction": True,
        "onePrimaryExecutionTarget": True,
        "onePrimaryOwner": True,
        "unresolvedDiagnosisBecomesObservation": True,
        "nativeObservationIsLegalTerminal": True,
        "diagnosisAuditOnlyDownstream": True,
    }
    return context


def _build_messages(
    data_version: str | None,
    batch: List[Dict[str, Any]],
    rag_context: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    payload = {
        "dataVersion": data_version,
        "version": THREE_AGENT_PIPELINE_VERSION,
        "products": [legacy._fact_card(item) for item in batch],
        "diagnosticRag": rag_context or build_agent1_rag_context(),
    }
    prompt = (
        "你是V22.5.5经营诊断Agent1。先做完整诊断，再决定observe或act。"
        "完整诊断可包含多个因果假设、反证和替代方向，但执行交接必须收敛。"
        "act仅在证据充分且能唯一锁定primaryProblemNode、primaryAction、primaryExecutionTarget、"
        "primaryOwner和decisiveFacts时允许；否则必须decisionType=observe、selectedActionFamilyHint=null、"
        "selectedOperatingRoute=observe、evidenceStatus=insufficient，并把原因写入missingEvidence。"
        "primaryExecutionTarget必须是对象，包含targetType、targetId和owner。"
        "跨部门事项只能进入supportingCoordination；禁止把详情页、客服、仓储作为并列主动作。"
        "库存只能作为容量约束或协同事实，不能单独证明经营原因。"
        "原生observe是合法终态，不需要构造执行锁，也不得伪装成失败。"
        "你不生成SOP、标题、预算、优惠券或执行步骤。"
        "每项原样返回correlationId/productId/storeId/signalId。只返回JSON对象，顶层judgments数组。"
        "字段：correlationId,productId,storeId,signalId,metricCode,severity,confidence,decisionHint,decisionType,"
        "finding,coreProblem,facts,causalHypotheses,rejectedHypotheses,decisionSummary,alternatives,"
        "selectedOperatingRoute,selectedActionFamilyHint,actionIntent,preconditions,riskBoundaries,missingEvidence,"
        "evidenceStatus,primaryProblemNode,primaryAction,primaryExecutionTarget,primaryOwner,decisiveFacts,"
        "supportingCoordination,forbiddenActionDomains,excludedActions,requiredActionData,capacityConstraints,"
        "companyHooks,ragProof,routeLock,actionFamilyLock。"
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    ], payload


def _raw_index(provider_payload: Dict[str, Any]) -> Dict[str, Dict[Any, Dict[str, Any]]]:
    by_correlation: Dict[Any, Dict[str, Any]] = {}
    by_exact: Dict[Any, Dict[str, Any]] = {}
    by_signal: Dict[Any, Dict[str, Any]] = {}
    for raw in _arr(provider_payload.get("judgments")):
        if not isinstance(raw, dict):
            continue
        correlation = _text(raw.get("correlationId"), 240)
        product = _text(raw.get("productId"), 180)
        store = _text(raw.get("storeId"), 180)
        signal = _text(raw.get("signalId"), 240)
        if correlation:
            by_correlation[correlation] = raw
        if product and store:
            by_exact[(store, product)] = raw
        if signal:
            by_signal[signal] = raw
    return {"byCorrelation": by_correlation, "byExact": by_exact, "bySignal": by_signal}


def _raw_for(item: Dict[str, Any], indexes: Dict[str, Dict[Any, Dict[str, Any]]]) -> Dict[str, Any]:
    correlation = _text(item.get("correlationId"), 240)
    exact = (_text(item.get("storeId"), 180), _text(item.get("productId"), 180))
    signal = _text(item.get("signalId"), 240)
    return (
        indexes["byCorrelation"].get(correlation)
        or indexes["byExact"].get(exact)
        or indexes["bySignal"].get(signal)
        or {}
    )


def _target(raw: Dict[str, Any], product_id: Any) -> Dict[str, Any]:
    source = _dict(raw.get("primaryExecutionTarget"))
    return {
        key: value
        for key, value in {
            "targetType": _text(source.get("targetType") or source.get("type"), 160),
            "targetId": _text(source.get("targetId") or source.get("id") or product_id, 220),
            "owner": _text(source.get("owner") or source.get("directOwner") or raw.get("primaryOwner"), 120),
            "scope": _text(source.get("scope") or source.get("targetScope"), 220),
        }.items()
        if value not in (None, "", [], {})
    }


def _observe_lock(raw: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
    status = _text(raw.get("evidenceStatus"), 40).lower()
    if status not in {EVIDENCE_SUFFICIENT, EVIDENCE_INSUFFICIENT, EVIDENCE_CONFLICT}:
        status = EVIDENCE_INSUFFICIENT
    return {
        "version": THREE_AGENT_PIPELINE_VERSION,
        "locked": False,
        "evidenceStatus": status,
        "missingEvidence": _arr(raw.get("missingEvidence"))[:12],
        "lockReason": reason,
        "singlePrimaryAction": True,
        "singlePrimaryExecutionTarget": True,
        "forbiddenOverride": True,
    }


def _preserve_observation(item: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    judgment = _dict(item.get("agent1OperatingJudgment"))
    ir = _dict(item.get("agent1DecisionIR"))
    lock = _observe_lock(raw, reason="Agent1 selected a native observation; no downstream execution is allowed.")
    missing_evidence = _arr(raw.get("missingEvidence"))[:12]
    ir.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        decisionType="observe",
        selectedActionFamily=None,
        evidenceStatus=lock.get("evidenceStatus"),
        missingEvidence=missing_evidence,
        executionLock=lock,
    )
    judgment.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        decisionType="observe",
        selectedOperatingRoute="observe",
        selectedActionFamily=None,
        evidenceStatus=lock.get("evidenceStatus"),
        executionLock=lock,
        routeLock={
            **_dict(judgment.get("routeLock")),
            "locked": True,
            "selectedOperatingRoute": "observe",
            "observationOnly": True,
            "lockReason": "Native observation selected by Agent1.",
        },
        actionFamilyLock={
            **_dict(judgment.get("actionFamilyLock")),
            "locked": True,
            "selectedActionFamily": None,
            "forbiddenOverride": True,
            "observationOnly": True,
            "lockReason": "Observation terminates before Action Matrix and Agent2.",
        },
    )
    item.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        decisionType="observe",
        decisionHint="observe_only",
        selectedOperatingRoute="observe",
        selectedActionFamilyHint=None,
        actionFamily=None,
        route="observe",
        evidenceStatus=lock.get("evidenceStatus"),
        executionLock=lock,
        missingEvidence=missing_evidence,
        observationOnly=True,
        diagnosticHold=False,
        diagnosticHoldReason="native_observation",
        taskAdmissionAllowed=False,
        agent1OperatingJudgment=judgment,
        agent1DecisionIR=ir,
    )
    return item


def _observe_unresolved(item: Dict[str, Any], raw: Dict[str, Any], missing: List[str]) -> Dict[str, Any]:
    judgment = _dict(item.get("agent1OperatingJudgment"))
    ir = _dict(item.get("agent1DecisionIR"))
    missing_evidence = list(dict.fromkeys([*_arr(raw.get("missingEvidence")), *missing]))
    lock = {
        "version": THREE_AGENT_PIPELINE_VERSION,
        "locked": False,
        "evidenceStatus": EVIDENCE_INSUFFICIENT,
        "missingEvidence": missing_evidence,
        "lockReason": "Agent1 attempted act but the execution lock was incomplete.",
        "singlePrimaryAction": True,
        "singlePrimaryExecutionTarget": True,
        "forbiddenOverride": True,
    }
    ir.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        decisionType="observe",
        selectedActionFamily=None,
        evidenceStatus=EVIDENCE_INSUFFICIENT,
        missingEvidence=missing_evidence,
        executionLock=lock,
    )
    judgment.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        decisionType="observe",
        selectedOperatingRoute="observe",
        selectedActionFamily=None,
        evidenceStatus=EVIDENCE_INSUFFICIENT,
        executionLock=lock,
        routeLock={
            **_dict(judgment.get("routeLock")),
            "locked": True,
            "selectedOperatingRoute": "observe",
            "observationOnly": True,
            "lockReason": "Agent1 diagnosis is unresolved; evidence-backed execution lock is incomplete.",
        },
        actionFamilyLock={
            **_dict(judgment.get("actionFamilyLock")),
            "locked": True,
            "selectedActionFamily": None,
            "forbiddenOverride": True,
            "observationOnly": True,
            "lockReason": "No action family may proceed without a complete execution lock.",
        },
    )
    item.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        decisionType="observe",
        decisionHint="observe_only",
        selectedOperatingRoute="observe",
        selectedActionFamilyHint=None,
        actionFamily=None,
        route="observe",
        evidenceStatus=EVIDENCE_INSUFFICIENT,
        executionLock=lock,
        missingEvidence=missing_evidence,
        observationOnly=True,
        diagnosticHold=True,
        diagnosticHoldReason="agent1_execution_lock_incomplete",
        taskAdmissionAllowed=False,
        agent1OperatingJudgment=judgment,
        agent1DecisionIR=ir,
    )
    return item


def _apply_execution_lock(item: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    if item.get("decisionType") != "act":
        return _preserve_observation(item, raw)

    judgment = _dict(item.get("agent1OperatingJudgment"))
    ir = _dict(item.get("agent1DecisionIR"))
    evidence_status = _text(raw.get("evidenceStatus"), 40).lower()
    if evidence_status not in {EVIDENCE_SUFFICIENT, EVIDENCE_INSUFFICIENT, EVIDENCE_CONFLICT}:
        evidence_status = EVIDENCE_INSUFFICIENT if _arr(raw.get("missingEvidence")) else ""
    target = _target(raw, item.get("productId"))
    lock_source = {
        **item,
        "evidenceStatus": evidence_status,
        "primaryProblemNode": _text(raw.get("primaryProblemNode"), 500),
        "primaryAction": _text(raw.get("primaryAction"), 500),
        "primaryExecutionTarget": target,
        "primaryOwner": _text(raw.get("primaryOwner") or target.get("owner"), 120),
        "decisiveFacts": _arr(raw.get("decisiveFacts"))[:12],
        "supportingCoordination": _arr(raw.get("supportingCoordination"))[:6],
        "forbiddenActionDomains": _arr(raw.get("forbiddenActionDomains"))[:12],
        "missingEvidence": _arr(raw.get("missingEvidence"))[:12],
    }
    lock = execution_lock_from(lock_source)
    missing = missing_execution_lock(lock)
    if missing:
        return _observe_unresolved(item, raw, missing)

    ir.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        evidenceStatus=EVIDENCE_SUFFICIENT,
        primaryProblemNode=lock.get("primaryProblemNode"),
        primaryAction=lock.get("primaryAction"),
        primaryExecutionTarget=lock.get("primaryExecutionTarget"),
        primaryOwner=lock.get("primaryOwner"),
        decisiveFacts=lock.get("decisiveFacts") or [],
        supportingCoordination=lock.get("supportingCoordination") or [],
        forbiddenActionDomains=lock.get("forbiddenActionDomains") or [],
        executionLock=lock,
    )
    judgment.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        evidenceStatus=EVIDENCE_SUFFICIENT,
        primaryProblemNode=lock.get("primaryProblemNode"),
        primaryAction=lock.get("primaryAction"),
        primaryExecutionTarget=lock.get("primaryExecutionTarget"),
        primaryOwner=lock.get("primaryOwner"),
        decisiveFacts=lock.get("decisiveFacts") or [],
        supportingCoordination=lock.get("supportingCoordination") or [],
        forbiddenActionDomains=lock.get("forbiddenActionDomains") or [],
        executionLock=lock,
    )
    item.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        evidenceStatus=EVIDENCE_SUFFICIENT,
        primaryProblemNode=lock.get("primaryProblemNode"),
        primaryAction=lock.get("primaryAction"),
        primaryExecutionTarget=lock.get("primaryExecutionTarget"),
        primaryOwner=lock.get("primaryOwner"),
        decisiveFacts=lock.get("decisiveFacts") or [],
        supportingCoordination=lock.get("supportingCoordination") or [],
        forbiddenActionDomains=lock.get("forbiddenActionDomains") or [],
        executionLock=lock,
        diagnosticHold=False,
        diagnosticHoldReason=None,
        taskAdmissionAllowed=True,
        agent1OperatingJudgment=judgment,
        agent1DecisionIR=ir,
    )
    return item


def _normalize_judgments(
    provider_payload: Dict[str, Any],
    source_maps: Dict[str, Any],
    data_version: str | None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    normalized, diagnostics = legacy._normalize_judgments(provider_payload, source_maps, data_version)
    indexes = _raw_index(provider_payload)
    resolved: List[Dict[str, Any]] = []
    converted_to_observe = 0
    native_observations = 0
    execution_locked = 0
    for current in normalized:
        item = dict(current)
        raw = _raw_for(item, indexes)
        before = str(item.get("decisionType") or "")
        item = _apply_execution_lock(item, raw)
        after = str(item.get("decisionType") or "")
        converted_to_observe += 1 if before == "act" and after == "observe" else 0
        native_observations += 1 if before == "observe" and after == "observe" else 0
        execution_locked += 1 if _dict(item.get("executionLock")).get("locked") is True else 0
        resolved.append(item)
    diagnostics.update(
        version=THREE_AGENT_PIPELINE_VERSION,
        executionLockContract="one_problem_one_action_one_owner_one_target",
        executionLockedCount=execution_locked,
        nativeObservationCount=native_observations,
        unresolvedActConvertedToObserveCount=converted_to_observe,
        diagnosticHypothesesAuditOnly=True,
    )
    return resolved, diagnostics


_source_maps = legacy._source_maps
_fact_card = legacy._fact_card

__all__ = [
    "REAL_PRODUCT_AGENT_V2255_VERSION",
    "PRODUCT_AGENT_MODE",
    "build_agent1_rag_context",
    "_build_messages",
    "_normalize_judgments",
    "_source_maps",
    "_fact_card",
]
