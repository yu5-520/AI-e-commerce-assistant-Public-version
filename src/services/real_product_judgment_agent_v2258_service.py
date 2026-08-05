"""V22.5.8 Agent1 evidence-continuity and output-contract normalization."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from src.services import real_product_judgment_agent_v2255_service as execution_legacy

REAL_PRODUCT_AGENT_V2258_VERSION = "22.5.8"
THREE_AGENT_PIPELINE_VERSION = "22.5.5"
PRODUCT_AGENT_MODE = "v22_5_8_lineage_trend_then_execution_lock"

OBSERVE_ALIASES = {
    "observe",
    "observation",
    "attention",
    "watch",
    "monitor",
    "hold",
    "observe_only",
    "metric_observation",
    "product_level_observation",
}
ACT_ALIASES = {
    "act",
    "action",
    "execute",
    "execution",
    "intervention",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 800) -> str:
    if isinstance(value, dict):
        value = value.get("summary") or value.get("text") or value.get("action") or value.get("id")
    return " ".join(str(value or "").split())[:limit]


def _payload(bundle: Dict[str, Any]) -> Dict[str, Any]:
    nested = bundle.get("payload")
    return nested if isinstance(nested, dict) and nested else bundle


def _profile(bundle: Dict[str, Any]) -> Dict[str, Any]:
    payload = _payload(bundle)
    return {**_dict(payload.get("profileLayer")), **_dict(payload.get("productIdentity"))}


def _field_signals(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = _payload(bundle)
    snapshot = _dict(payload.get("snapshotLayer"))
    values = snapshot.get("fieldSignals") or payload.get("fieldSignals") or []
    return [dict(item) for item in _arr(values) if isinstance(item, dict)]


def _strict_product_id(bundle: Dict[str, Any]) -> str:
    payload = _payload(bundle)
    profile = _profile(bundle)
    return _text(bundle.get("productId") or payload.get("productId") or profile.get("productId"), 180)


def _store_id(bundle: Dict[str, Any]) -> str:
    payload = _payload(bundle)
    profile = _profile(bundle)
    return _text(bundle.get("storeId") or payload.get("storeId") or profile.get("storeId"), 180)


def _signal_id(bundle: Dict[str, Any]) -> str:
    payload = _payload(bundle)
    return _text(bundle.get("signalId") or payload.get("signalId"), 220)


def _correlation_id(bundle: Dict[str, Any]) -> str:
    payload = _payload(bundle)
    return _text(
        bundle.get("correlationId")
        or payload.get("correlationId")
        or f"{_store_id(bundle)}:{_strict_product_id(bundle)}:{_signal_id(bundle)}",
        240,
    )


def _fact_card(bundle: Dict[str, Any]) -> Dict[str, Any]:
    payload = _payload(bundle)
    profile = _profile(bundle)
    signals = _field_signals(bundle)
    metric_layer = dict(_dict(payload.get("metricLayer")))
    metric_layer.pop("fieldSignals", None)
    trend_context = _dict(payload.get("trendContext"))
    cross_validation = _dict(payload.get("crossValidation"))
    fact_validation = _dict(payload.get("factLayerValidation"))
    lineage = _dict(payload.get("sourceLineageValidation"))
    return {
        "correlationId": _correlation_id(bundle),
        "productId": _strict_product_id(bundle),
        "storeId": _store_id(bundle),
        "signalId": _signal_id(bundle) or None,
        "title": profile.get("productTitle") or profile.get("title") or payload.get("title"),
        "platform": profile.get("platform") or payload.get("platform"),
        "verticalCategory": profile.get("verticalCategory") or payload.get("verticalCategory"),
        "productRole": profile.get("productRole"),
        "lifecycleStage": profile.get("lifecycleStage"),
        "metricDate": metric_layer.get("metricDate") or profile.get("metricDate"),
        "fieldSignals": signals,
        "signalSummary": {
            "fieldSignalCount": len(signals),
            "meaningfulSignalCount": sum(1 for item in signals if item.get("meaningfulChange") is True),
            "strongSignalCount": sum(
                1
                for item in signals
                if str(item.get("signalStrength") or "").lower() in {"strong", "high", "critical"}
            ),
            "sourceVersionCount": int(lineage.get("sourceVersionCount") or 0),
            "sourceDatasetCount": int(lineage.get("sourceDatasetCount") or 0),
            "changedMetricCount": int(cross_validation.get("changedMetricCount") or 0),
            "abnormalMetricCount": int(cross_validation.get("abnormalMetricCount") or 0),
        },
        "metricSnapshot": metric_layer,
        "trendContext": trend_context,
        "sourceLineageValidation": lineage,
        "sourceValidationStatus": lineage.get("status"),
        "strongRelations": payload.get("strongRelations") or payload.get("relationFacts") or [],
        "crossValidation": cross_validation,
        "factLayerValidation": fact_validation,
        "dataFingerprint": payload.get("dataFingerprint") or bundle.get("dataFingerprint"),
        "inputProjectionVersion": (_dict(payload.get("inputContract"))).get("projectionVersion"),
    }


def build_agent1_rag_context() -> Dict[str, Any]:
    context = execution_legacy.build_agent1_rag_context()
    principles = list(context.get("principles") or [])
    principles.extend(
        [
            "来源身份只读取sourceLineageValidation；crossValidation只负责指标交叉验证，不得自行判定来源身份。",
            "sourceLineageValidation.sourceIdentityComplete=true时，禁止声称source identity incomplete或索要已存在的来源版本。",
            "必须优先使用trendContext中的mom、yoy、连续方向、窗口、斜率、波动率、主证据与关联证据。",
            "decisionType只能返回精确枚举observe或act；禁止返回observation、attention、watch、hold、action等同义词。",
            "低风险、可逆、可复盘的运营测试，在三期同向变化且关键指标交叉验证成立时可以act；不要求证明唯一终极因果。",
            "高风险、不可逆或大额动作仍必须具备完整因果证据、权限边界和执行锁。",
        ]
    )
    context["version"] = REAL_PRODUCT_AGENT_V2258_VERSION
    context["mode"] = PRODUCT_AGENT_MODE
    context["principles"] = list(dict.fromkeys(principles))
    context["guardrails"] = {
        **_dict(context.get("guardrails")),
        "sourceLineageSingleOwner": True,
        "crossValidationOwnsLineage": False,
        "semanticTrendContextRequired": True,
        "decisionTypeExactEnum": ["observe", "act"],
        "knownDecisionAliasesNormalizedByRuntime": True,
        "unknownDecisionFailClosedToObserve": True,
        "reversibleTestMayActOnCrossValidatedTrend": True,
        "irreversibleActionRequiresFullCausalProof": True,
        "duplicateSignalEvidenceForbidden": True,
    }
    return context


def _build_messages(
    data_version: str | None,
    batch: List[Dict[str, Any]],
    rag_context: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    payload = {
        "dataVersion": data_version,
        "version": REAL_PRODUCT_AGENT_V2258_VERSION,
        "products": [_fact_card(item) for item in batch],
        "diagnosticRag": rag_context or build_agent1_rag_context(),
    }
    prompt = (
        "你是V22.5.8经营诊断Agent1。输入已提供完整fieldSignals、trendContext、"
        "sourceLineageValidation、crossValidation和factLayerValidation。"
        "来源身份只以sourceLineageValidation为准；当sourceIdentityComplete=true时，"
        "禁止声称source identity incomplete，也禁止索要输入中已有的来源版本、历史期数和趋势字段。"
        "crossValidation只用于指标交叉验证。必须读取mom、yoy、连续方向、窗口、斜率、"
        "波动率、主证据和关联证据，再决定动作。"
        "decisionType必须且只能是字符串observe或act；禁止返回observation、attention、watch、"
        "monitor、hold、action、execution等同义词。"
        "低风险、可逆、可复盘测试，在至少三期同向变化且结果指标与原因指标交叉验证成立时可以act；"
        "高风险、不可逆、大额预算、下架或大幅降价动作仍要求完整因果证据。"
        "act必须唯一锁定primaryProblemNode、primaryAction、primaryExecutionTarget、"
        "primaryOwner和decisiveFacts；无法形成唯一执行交接时observe。"
        "每项原样返回correlationId/productId/storeId/signalId。只返回JSON对象，顶层judgments数组。"
        "字段：correlationId,productId,storeId,signalId,metricCode,severity,confidence,"
        "decisionHint,decisionType,finding,coreProblem,facts,causalHypotheses,rejectedHypotheses,"
        "decisionSummary,alternatives,selectedOperatingRoute,selectedActionFamilyHint,actionIntent,"
        "preconditions,riskBoundaries,missingEvidence,evidenceStatus,primaryProblemNode,primaryAction,"
        "primaryExecutionTarget,primaryOwner,decisiveFacts,supportingCoordination,forbiddenActionDomains,"
        "excludedActions,requiredActionData,capacityConstraints,companyHooks,ragProof,routeLock,actionFamilyLock。"
    )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ], payload


def _identity_key(raw: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _text(raw.get("correlationId"), 240),
        _text(raw.get("storeId"), 180),
        _text(raw.get("productId"), 180),
        _text(raw.get("signalId"), 240),
    )


def _canonicalize_raw(raw: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    item = dict(raw)
    original = _text(item.get("decisionType"), 80).lower()
    hint = _text(item.get("decisionHint"), 80).lower()
    candidate = original or hint
    warning = ""
    if candidate in OBSERVE_ALIASES:
        canonical = "observe"
        if candidate != "observe":
            warning = f"decision_alias_normalized:{candidate}->observe"
    elif candidate in ACT_ALIASES:
        canonical = "act"
        if candidate != "act":
            warning = f"decision_alias_normalized:{candidate}->act"
    else:
        canonical = "observe"
        warning = f"decision_type_invalid_fail_closed:{candidate or 'missing'}->observe"
    item["decisionType"] = canonical
    item["rawDecisionType"] = original or None
    if canonical == "act":
        family = _text(item.get("selectedActionFamilyHint"), 160)
        route = _text(item.get("selectedOperatingRoute"), 160)
        if not family or not route or route == "observe":
            canonical = "observe"
            warning = "act_route_contract_incomplete_fail_closed:act->observe"
            item["decisionType"] = canonical
    if canonical == "observe":
        item["decisionHint"] = "observe_only"
        item["selectedOperatingRoute"] = "observe"
        item["selectedActionFamilyHint"] = None
        missing = list(_arr(item.get("missingEvidence")))
        if warning.startswith("decision_type_invalid"):
            missing.append("decisionType未遵守observe|act合同，系统已安全降级为观察")
        if warning.startswith("act_route_contract_incomplete"):
            missing.append("动作意图存在，但动作族或经营路由未形成合法合同，系统已安全降级为观察")
        item["missingEvidence"] = list(dict.fromkeys(missing))[:12]
    elif not item.get("decisionHint"):
        item["decisionHint"] = "action_candidate"
    return item, {
        "identity": _identity_key(item),
        "rawDecisionType": original or None,
        "canonicalDecisionType": canonical,
        "warning": warning or None,
    }


def _warning_for(item: Dict[str, Any], warnings: List[Dict[str, Any]]) -> Dict[str, Any]:
    correlation = _correlation_id(item)
    store = _store_id(item)
    product = _strict_product_id(item)
    signal = _signal_id(item)
    for warning in warnings:
        key = warning.get("identity") or ()
        if correlation and key[0] == correlation:
            return warning
        if store and product and key[1:3] == (store, product):
            return warning
        if signal and key[3] == signal:
            return warning
    return {}


def _normalize_judgments(
    provider_payload: Dict[str, Any],
    source_maps: Dict[str, Any],
    data_version: str | None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    raw_items = provider_payload.get("judgments") if isinstance(provider_payload, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("provider_json_missing_judgments_array")
    canonical_items: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            canonical_items.append(raw)
            continue
        item, warning = _canonicalize_raw(raw)
        canonical_items.append(item)
        warnings.append(warning)
    canonical_payload = {**provider_payload, "judgments": canonical_items}
    normalized, diagnostics = execution_legacy._normalize_judgments(
        canonical_payload,
        source_maps,
        data_version,
    )
    alias_count = 0
    fail_closed_count = 0
    for item in normalized:
        warning = _warning_for(item, warnings)
        text = str(warning.get("warning") or "")
        if text:
            item["rawDecisionType"] = warning.get("rawDecisionType")
            item["normalizationWarnings"] = [text]
            item["normalizationStatus"] = "normalized_with_warning"
            if text.startswith("act_route_contract_incomplete"):
                item["diagnosticHold"] = True
                item["diagnosticHoldReason"] = "agent1_output_contract_incomplete"
            alias_count += 1 if text.startswith("decision_alias_normalized") else 0
            fail_closed_count += 1 if (
                text.startswith("decision_type_invalid_fail_closed")
                or text.startswith("act_route_contract_incomplete")
            ) else 0
        else:
            item["normalizationWarnings"] = []
            item["normalizationStatus"] = "normalized"
        item["decisionContractStatus"] = "valid"
        item["sourceValidationStatus"] = "complete"
    diagnostics.update(
        version=REAL_PRODUCT_AGENT_V2258_VERSION,
        providerResponseItemCount=len(raw_items),
        normalizationStatus=(
            "normalized_with_warning"
            if alias_count or fail_closed_count
            else "normalized"
        ),
        decisionAliasNormalizedCount=alias_count,
        unknownDecisionFailClosedCount=fail_closed_count,
        outputContractInvalidCount=int(
            diagnostics.get("invalidProviderContractCount") or 0
        ),
        providerCallStatusOwnedByTokenRuntime=True,
        noMatchingJudgmentMeansIdentityAbsentOnly=True,
    )
    return normalized, diagnostics


_source_maps = execution_legacy._source_maps

__all__ = [
    "REAL_PRODUCT_AGENT_V2258_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "PRODUCT_AGENT_MODE",
    "OBSERVE_ALIASES",
    "ACT_ALIASES",
    "build_agent1_rag_context",
    "_build_messages",
    "_normalize_judgments",
    "_source_maps",
    "_fact_card",
]
