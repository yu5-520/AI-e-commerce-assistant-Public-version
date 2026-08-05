"""V22 contextual Agent1 core.

Agent1 receives current product facts plus reviewed operating experience, compares
causal hypotheses, and returns either a native observation or one immutable
primary action-family lock. No deterministic business fallback is created.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Tuple

from src.repositories.sqlite_repository import connect, loads
from src.runtime_version import VERSION
from src.services.llm_gateway_v196_service import call_json
from src.services.signal_pool_service import list_signals

REAL_PRODUCT_AGENT_V196_VERSION = VERSION
PRODUCT_AGENT_MODE = "v22_contextual_diagnosis_before_family_lock"
MAX_PRODUCTS_PER_CALL = int(os.getenv("PRODUCT_JUDGMENT_AGENT_BATCH_SIZE", "8"))
MAX_PRODUCT_AGENT_CALLS_PER_RUN = int(os.getenv("PRODUCT_JUDGMENT_AGENT_MAX_CALLS", "8"))
TIMEOUT_SECONDS = int(os.getenv("PRODUCT_JUDGMENT_AGENT_TIMEOUT", "180"))
ALLOWED_SEVERITY = {"normal", "low", "medium", "high", "critical"}
OBSERVE_HINTS = {"observe_only", "metric_observation", "product_level_observation"}
ALLOWED_HINTS = {"risk_candidate", "related_risk", "data_gap_candidate", *OBSERVE_HINTS}
ALLOWED_ACTION_FAMILIES = {
    "title_image_test",
    "roas_scale",
    "roas_guard",
    "platform_activity",
    "activity_apply",
    "conversion_repair",
    "service_repair",
    "similar_product_test",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _chunks(items: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    step = max(1, size)
    for index in range(0, len(items), step):
        yield items[index : index + step]


def _payload(bundle: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(bundle.get("payload")) or bundle


def _strict_product_id(bundle: Dict[str, Any]) -> str:
    payload = _payload(bundle)
    profile = _dict(payload.get("profileLayer"))
    identity = _dict(payload.get("productIdentity"))
    return _text(
        bundle.get("productId")
        or bundle.get("entityId")
        or payload.get("productId")
        or identity.get("productId")
        or profile.get("productId")
    )


def _store_id(bundle: Dict[str, Any]) -> str:
    payload = _payload(bundle)
    profile = _dict(payload.get("profileLayer"))
    identity = _dict(payload.get("productIdentity"))
    return _text(
        bundle.get("storeId")
        or payload.get("storeId")
        or identity.get("storeId")
        or profile.get("storeId")
    )


def _signal_id(bundle: Dict[str, Any]) -> str:
    payload = _payload(bundle)
    return _text(
        bundle.get("signalId")
        or bundle.get("signal_id")
        or payload.get("signalId")
        or payload.get("signal_id")
    )


def _profile(bundle: Dict[str, Any]) -> Dict[str, Any]:
    payload = _payload(bundle)
    return {**_dict(payload.get("profileLayer")), **_dict(payload.get("productIdentity"))}


def _metric(bundle: Dict[str, Any]) -> Dict[str, Any]:
    payload = _payload(bundle)
    return {
        **_dict(payload.get("metricLayer")),
        **_dict(payload.get("snapshotLayer")),
        **_dict(payload.get("dynamicMetrics")),
    }


def _field_signals(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = _payload(bundle)
    snapshot = _dict(payload.get("snapshotLayer"))
    values = snapshot.get("fieldSignals") or payload.get("fieldSignals") or bundle.get("fieldSignals") or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _fact_line(item: Dict[str, Any]) -> str:
    code = item.get("metricCode") or item.get("code") or item.get("metricName") or "metric"
    current = item.get("current", item.get("currentValue", item.get("latest")))
    previous = item.get("previous", item.get("previousValue"))
    delta = item.get("changeRatio", item.get("changeRate", item.get("deltaRate")))
    parts = [str(code), f"previous={previous}", f"current={current}"]
    if delta is not None:
        parts.append(f"change={delta}")
    if item.get("reason"):
        parts.append(f"reason={item.get('reason')}")
    return ", ".join(parts)


def _correlation_id(bundle: Dict[str, Any]) -> str:
    explicit = bundle.get("correlationId") or bundle.get("pipelineItemId") or bundle.get("itemId")
    if explicit:
        return _text(explicit)
    raw = "|".join([_store_id(bundle), _strict_product_id(bundle), _signal_id(bundle)])
    return "A1C-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20].upper()


def _fact_card(bundle: Dict[str, Any]) -> Dict[str, Any]:
    payload = _payload(bundle)
    profile = _profile(bundle)
    metric = _metric(bundle)
    return {
        "correlationId": _correlation_id(bundle),
        "productId": _strict_product_id(bundle),
        "storeId": _store_id(bundle),
        "signalId": _signal_id(bundle) or None,
        "title": profile.get("productTitle") or profile.get("title") or payload.get("productTitle") or payload.get("title"),
        "platform": profile.get("platform") or payload.get("platform"),
        "verticalCategory": profile.get("verticalCategory") or payload.get("verticalCategory"),
        "productRole": profile.get("productRole"),
        "lifecycleStage": profile.get("lifecycleStage"),
        "metricDate": metric.get("metricDate") or profile.get("metricDate"),
        "factDigest": [_fact_line(item) for item in _field_signals(bundle)[:16]],
        "metricSnapshot": metric,
        "strongRelations": payload.get("strongRelations") or payload.get("relationFacts") or [],
        "crossValidation": _dict(payload.get("crossValidation")),
        "dataFingerprint": payload.get("dataFingerprint") or bundle.get("dataFingerprint"),
    }


def _experience_cards(limit: int = 16) -> List[Dict[str, Any]]:
    try:
        with connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rag_experience_cards'"
            ).fetchone()
            if not exists:
                return []
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(rag_experience_cards)")}
            where = ""
            if "status" in columns:
                where = " WHERE lower(COALESCE(status,'')) IN ('approved','effective','validated','learned')"
            order = "updated_at DESC" if "updated_at" in columns else "rowid DESC"
            rows = conn.execute(
                f"SELECT * FROM rag_experience_cards{where} ORDER BY {order} LIMIT ?",
                (max(1, min(40, int(limit))),),
            ).fetchall()
    except Exception:
        return []
    cards: List[Dict[str, Any]] = []
    for row in rows:
        source = dict(row)
        for key in ("payload", "card", "content", "experience"):
            value = source.get(key)
            if isinstance(value, str) and value.strip().startswith(("{", "[")):
                try:
                    parsed = loads(value)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    source.update(parsed)
            elif isinstance(value, dict):
                source.update(value)
        card = {
            "caseId": source.get("case_id") or source.get("caseId") or source.get("id"),
            "problemFrame": source.get("problemFrame") or source.get("scenario") or source.get("summary"),
            "causalPattern": source.get("causalPattern") or source.get("principle") or source.get("decisionReason"),
            "supportingSignals": source.get("supportingSignals") or source.get("applicableConditions") or [],
            "opposingSignals": source.get("opposingSignals") or source.get("notApplicableConditions") or [],
            "rejectedAlternative": source.get("rejectedAlternative") or source.get("failedApproach"),
            "resultSummary": source.get("resultSummary") or source.get("result"),
        }
        if any(value not in (None, "", [], {}) for value in card.values()):
            cards.append(card)
    return cards


def build_agent1_rag_context() -> Dict[str, Any]:
    context = {
        "version": VERSION,
        "mode": "diagnostic_rag_before_action_family_lock",
        "principles": [
            "先解释发生了什么，再比较可能原因，最后才选择动作族。",
            "结果指标、原因指标、约束条件和推断必须分开。",
            "单点指标不能独立决定任务，趋势和强关联指标需要交叉验证。",
            "库存是供应承接前置条件或协同事实，不是投放绩效下降的证明。",
            "观察是合法终态，证据不足时不得借默认动作族进入任务链。",
            "正式act结果只能锁定一个主动作族。",
            "RAG只能提供经验和反例，不能覆盖当前事实、权限或公司底线。",
        ],
        "guardrails": {
            "onePrimaryActionFamily": True,
            "observeWithoutActionFamily": True,
            "inventoryTrafficCutoffForbidden": True,
            "fabricatedExecutionObjectForbidden": True,
            "crossAccountActionForbidden": True,
        },
        "experienceCards": _experience_cards(),
    }
    context["queryFingerprint"] = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return context


def _build_messages(
    data_version: str | None,
    batch: List[Dict[str, Any]],
    rag_context: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    payload = {
        "dataVersion": data_version,
        "version": VERSION,
        "products": [_fact_card(item) for item in batch],
        "diagnosticRag": rag_context or build_agent1_rag_context(),
    }
    prompt = (
        "你是V22经营诊断Agent1，不是规则分类器。代码只提供事实、指标关系、经验和底线，"
        "不得把任何图谱或候选模式当成预选答案。对每个商品先识别核心变化，区分结果指标与原因指标，"
        "形成多个因果假设，分别列出支持事实与反证，排除不成立路径，再决定act或observe。"
        "RAG案例只用于类比、反例和边界，不得复制历史结论。observe时动作族必须为null并终止；"
        "act时只能锁定一个主动作族。库存只能作为前置条件、capacityConstraints或跨部门协同，"
        "不能证明ROI恶化，也不能单独触发断流。你不生成SOP、标题、预算、优惠券或执行步骤。"
        "每项必须原样返回correlationId/productId/storeId/signalId。只返回JSON对象，顶层judgments数组。"
        "字段：correlationId,productId,storeId,signalId,metricCode,severity,confidence,decisionHint,decisionType,"
        "finding,coreProblem,facts,causalHypotheses,rejectedHypotheses,decisionSummary,alternatives,"
        "selectedOperatingRoute,selectedActionFamilyHint,actionIntent,preconditions,riskBoundaries,missingEvidence,"
        "excludedActions,requiredActionData,capacityConstraints,companyHooks,ragProof,routeLock,actionFamilyLock。"
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    ], payload


def _source_maps(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "byCorrelation": {_correlation_id(item): item for item in signals},
        "byExact": {(_store_id(item), _strict_product_id(item)): item for item in signals},
        "bySignal": {_signal_id(item): item for item in signals if _signal_id(item)},
    }


def _resolve_source(raw: Dict[str, Any], maps: Dict[str, Any], seen: set[int]) -> tuple[Dict[str, Any] | None, str]:
    correlation = _text(raw.get("correlationId"))
    if correlation and correlation in maps["byCorrelation"]:
        item = maps["byCorrelation"][correlation]
        return (None, "duplicate") if id(item) in seen else (item, "correlationId")
    exact = (_text(raw.get("storeId")), _text(raw.get("productId")))
    if all(exact) and exact in maps["byExact"]:
        item = maps["byExact"][exact]
        return (None, "duplicate") if id(item) in seen else (item, "storeId+productId")
    signal_id = _text(raw.get("signalId"))
    if signal_id and signal_id in maps["bySignal"]:
        item = maps["bySignal"][signal_id]
        return (None, "duplicate") if id(item) in seen else (item, "signalId")
    return None, "unmatched"


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    return round(max(0.0, min(0.99, number)), 4)


def _normalize_judgments(
    provider_payload: Dict[str, Any],
    source_maps: Dict[str, Any],
    data_version: str | None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    raw_items = provider_payload.get("judgments") if isinstance(provider_payload, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("provider_json_missing_judgments_array")
    normalized: List[Dict[str, Any]] = []
    seen: set[int] = set()
    unmatched: List[Dict[str, Any]] = []
    invalid = duplicate = 0
    matched_by: Dict[str, int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            invalid += 1
            continue
        source, match_mode = _resolve_source(raw, source_maps, seen)
        if not source:
            duplicate += 1 if match_mode == "duplicate" else 0
            if match_mode != "duplicate":
                unmatched.append({key: raw.get(key) for key in ("correlationId", "productId", "storeId", "signalId")})
            continue
        seen.add(id(source))
        matched_by[match_mode] = matched_by.get(match_mode, 0) + 1
        hint = _text(raw.get("decisionHint") or "risk_candidate")
        decision_type = _text(raw.get("decisionType")).lower()
        if not decision_type:
            decision_type = "observe" if hint in OBSERVE_HINTS else "act"
        family = _text((raw.get("lockedActionFamily") or raw.get("selectedActionFamilyHint"))) or None
        route = _text(raw.get("selectedOperatingRoute"))
        if decision_type == "observe":
            hint = "observe_only"
            family = None
            route = "observe"
        elif family not in ALLOWED_ACTION_FAMILIES or not route or route == "observe":
            invalid += 1
            continue
        severity = _text(raw.get("severity") or "normal").lower()
        if severity not in ALLOWED_SEVERITY:
            severity = "normal"
        finding = _text(raw.get("finding"))
        reason = _text(raw.get("decisionSummary") or finding or "继续观察")
        facts = _arr(raw.get("facts")) or [
            {"factRef": f"F{index + 1}", "role": "evidence", "text": _text(value)}
            for index, value in enumerate(_arr(raw.get("evidence")))
            if _text(value)
        ]
        route_lock = {
            "locked": True,
            "selectedOperatingRoute": route,
            "lockReason": reason,
            "observationOnly": decision_type == "observe",
        }
        family_lock = {
            "locked": True,
            "selectedActionFamily": family,
            "lockReason": reason,
            "forbiddenOverride": True,
            "observationOnly": decision_type == "observe",
        }
        decision_ir = {
            "version": VERSION,
            "decisionType": decision_type,
            "coreProblem": _text(raw.get("coreProblem") or finding or reason),
            "facts": facts,
            "causalHypotheses": _arr(raw.get("causalHypotheses")),
            "rejectedHypotheses": _arr(raw.get("rejectedHypotheses")),
            "decisionSummary": reason,
            "alternatives": _arr(raw.get("alternatives")),
            "selectedActionFamily": family,
            "actionIntent": _text(raw.get("actionIntent")) or None,
            "preconditions": _arr(raw.get("preconditions")),
            "riskBoundaries": _arr(raw.get("riskBoundaries")),
            "missingEvidence": _arr(raw.get("missingEvidence")),
            "ragProof": _dict(raw.get("ragProof")),
        }
        judgment = {
            "stage": "agent1_contextual_diagnosis",
            "version": VERSION,
            "displayInDetail": True,
            "decisionType": decision_type,
            "selectedOperatingRoute": route,
            "selectedActionFamily": family,
            "primaryBusinessSignal": raw.get("primaryBusinessSignal") or finding,
            "primaryOperatingGap": raw.get("primaryOperatingGap") or decision_ir["coreProblem"],
            "businessHypothesis": raw.get("businessHypothesis") or decision_ir["coreProblem"],
            "evidenceFacts": _arr(raw.get("evidence")),
            "excludedActions": _arr(raw.get("excludedActions")),
            "requiredActionData": _arr(raw.get("requiredActionData")),
            "capacityConstraints": _arr(raw.get("capacityConstraints")),
            "companyHooks": _arr(raw.get("companyHooks")),
            "routeLock": route_lock,
            "actionFamilyLock": family_lock,
            "agent1DecisionIR": decision_ir,
        }
        normalized.append(
            {
                "version": VERSION,
                "dataVersion": data_version or source.get("dataVersion"),
                "correlationId": _correlation_id(source),
                "storeId": _store_id(source),
                "productId": _strict_product_id(source),
                "signalId": _signal_id(source) or None,
                "metricCode": _text(raw.get("metricCode") or "all_metrics"),
                "severity": severity,
                "decisionHint": hint,
                "decisionType": decision_type,
                "confidence": _confidence(raw.get("confidence")),
                "finding": finding or reason,
                "selectedOperatingRoute": route,
                "lockedActionFamily": family,
                "businessHypothesis": judgment["businessHypothesis"],
                "agent1OperatingJudgment": judgment,
                "agent1DecisionIR": decision_ir,
                "routeLock": route_lock,
                "actionFamilyLock": family_lock,
                "requiredActionData": judgment["requiredActionData"],
                "capacityConstraints": judgment["capacityConstraints"],
                "companyHooks": judgment["companyHooks"],
                "identityResolution": {"mode": match_mode, "canonical": True},
                "evidence": {"agentEvidence": judgment["evidenceFacts"][:10], "source": "v22_real_agent1"},
                "signal": source,
                "agent1ApiCallCount": 1,
                "ragRetrievalScope": "diagnostic_rag_before_agent1",
                "observationOnly": decision_type == "observe",
                "taskAdmissionAllowed": decision_type == "act",
                "fallbackAllowed": False,
                "rule": "V22 Agent1 returns a native observation or one canonical action-family lock.",
            }
        )
    return normalized, {
        "providerJudgmentCount": len(raw_items),
        "normalizedJudgmentCount": len(normalized),
        "unmatchedProviderJudgmentCount": len(unmatched),
        "unmatchedProviderItems": unmatched[:20],
        "duplicateProviderJudgmentCount": duplicate,
        "invalidProviderContractCount": invalid,
        "matchedBy": matched_by,
        "nativeObservationCount": sum(1 for item in normalized if item.get("decisionType") == "observe"),
        "temporaryActionFamilyUsed": False,
        "agent1DecisionIRVersion": VERSION,
    }


def _real_agent_judgments(
    signals: List[Dict[str, Any]],
    data_version: str | None,
    rag_context: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    valid = [item for item in signals if _strict_product_id(item) and _store_id(item)]
    if not valid:
        return [], {"providerStatus": "no_resolved_products", "actualCalls": 0, "fallbackUsed": False, "mode": PRODUCT_AGENT_MODE}
    judgments: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    errors: List[str] = []
    actual_calls = cache_hits = attempted = input_tokens = output_tokens = 0
    context = rag_context or build_agent1_rag_context()
    for index, batch in enumerate(_chunks(valid, MAX_PRODUCTS_PER_CALL)):
        if index >= MAX_PRODUCT_AGENT_CALLS_PER_RUN:
            errors.append("agent1_call_budget_reached_remaining_products_skipped")
            break
        attempted += 1
        try:
            messages, cache_payload = _build_messages(data_version, batch, context)
            payload, usage = call_json(
                stage="product_judgment_agent",
                prompt_version=VERSION,
                messages=messages,
                temperature=0.08,
                timeout_seconds=TIMEOUT_SECONDS,
                cache_payload=cache_payload,
                cache_enabled=True,
            )
            cache_hit = bool(usage.get("cacheHit"))
            actual_calls += 0 if cache_hit else 1
            cache_hits += 1 if cache_hit else 0
            input_tokens += int(usage.get("input") or 0)
            output_tokens += int(usage.get("output") or 0)
            batch_items, batch_diag = _normalize_judgments(payload, _source_maps(batch), data_version)
            judgments.extend(batch_items)
            diagnostics.append(batch_diag)
        except Exception as exc:
            errors.append(str(exc)[:500])
    status = "ok" if len(judgments) == len(valid) and not errors else "partial" if judgments else "failed"
    return judgments, {
        "providerStatus": status,
        "actualCalls": actual_calls,
        "cacheHits": cache_hits,
        "attemptedBatches": attempted,
        "errors": errors,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "inputProductCount": len(valid),
        "normalizedJudgmentCount": len(judgments),
        "missingProductJudgmentCount": max(0, len(valid) - len(judgments)),
        "batchDiagnostics": diagnostics,
        "mode": PRODUCT_AGENT_MODE,
        "model": os.getenv("PRODUCT_JUDGMENT_AGENT_MODEL") or os.getenv("QWEN_MODEL") or "qwen3.7-plus",
        "fallbackUsed": False,
        "cacheAllowed": True,
        "version": VERSION,
    }


def product_judgment_agent_station_v196(
    data_version: str | None,
    *,
    max_signals: int = 160,
    **_: Any,
) -> Dict[str, Any]:
    signals = (list_signals(data_version=data_version, status="pending_rag_agent", limit=max_signals).get("signals") or [])[:max_signals]
    judgments, provider = _real_agent_judgments(signals, data_version, build_agent1_rag_context())
    return {
        "version": VERSION,
        "stationId": "product_judgment_agent_station",
        "dataVersion": data_version,
        "inputBundleCount": len(signals),
        "agentJudgmentCount": len(judgments),
        "agent1ApiCallCount": int(provider.get("actualCalls") or 0),
        "productAgentProviderStatus": provider.get("providerStatus"),
        "productAgentProvider": provider,
        "judgments": judgments[:50],
        "rule": "V22 contextual Agent1 uses canonical identity binding and has no business fallback.",
    }


__all__ = [
    "REAL_PRODUCT_AGENT_V196_VERSION",
    "PRODUCT_AGENT_MODE",
    "ALLOWED_ACTION_FAMILIES",
    "build_agent1_rag_context",
    "_fact_card",
    "_real_agent_judgments",
    "_strict_product_id",
    "product_judgment_agent_station_v196",
]
