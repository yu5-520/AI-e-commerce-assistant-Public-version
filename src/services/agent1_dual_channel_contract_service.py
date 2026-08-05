"""V22 Agent1 dual-channel judgment contract.

The fixed decision core is the only authoritative Agent2 judgment source.
Agent-defined diagnostic extensions remain context-only enhancements for the
current item. Extensions can enrich execution detail but cannot override the
Agent1 action-family lock, system facts, permissions or numeric limits.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List, Tuple

from src.runtime_version import VERSION

AGENT1_DUAL_CHANNEL_CONTRACT_VERSION = VERSION
MAX_DIAGNOSTIC_EXTENSIONS = 12
MAX_AGENT2_EXTENSIONS = 8

_BOUND = False
_ORIGINALS: Dict[str, Callable[..., Any]] = {}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit] if limit and len(text) > limit else text


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    return round(max(0.0, min(0.99, number)), 4)


def _json_text(value: Any, limit: int = 800) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return _text(text, limit)


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity_key(raw: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _text(raw.get("correlationId")),
        _text(raw.get("storeId")),
        _text(raw.get("productId")),
        _text(raw.get("signalId")),
    )


def _decision_core(raw: Dict[str, Any], allowed_families: set[str]) -> Dict[str, Any]:
    supplied = _dict(raw.get("decisionCore"))
    diagnosis = _dict(raw.get("diagnosis"))
    decision_type = _text(
        supplied.get("decisionType")
        or supplied.get("type")
        or raw.get("decisionType")
    ).lower()
    if decision_type not in {"act", "observe"}:
        raise ValueError("agent1_decision_type_missing_or_invalid")

    finding = _text(raw.get("finding"))
    core_problem = _text(
        supplied.get("coreProblem")
        or diagnosis.get("coreProblem")
        or raw.get("coreProblem")
        or finding
    )
    summary = _text(
        supplied.get("decisionSummary")
        or supplied.get("summary")
        or raw.get("decisionSummary")
        or finding
    )
    if not core_problem:
        raise ValueError("agent1_decision_core_problem_missing")
    if not summary:
        raise ValueError("agent1_decision_summary_missing")

    family = _text(
        supplied.get("selectedActionFamily")
        or supplied.get("actionFamily")
        or raw.get("selectedActionFamily")
        or raw.get("selectedActionFamilyHint")
    ) or None
    route = _text(
        supplied.get("selectedOperatingRoute")
        or supplied.get("operatingRoute")
        or raw.get("selectedOperatingRoute")
    )
    if decision_type == "observe":
        family = None
        route = "observe"
    else:
        if family not in allowed_families:
            raise ValueError("agent1_action_family_missing_or_invalid")
        if not route or route == "observe":
            raise ValueError("agent1_operating_route_missing_or_invalid")

    return {
        "version": VERSION,
        "decisionType": decision_type,
        "confidence": _confidence(supplied.get("confidence", raw.get("confidence"))),
        "coreProblem": core_problem,
        "decisionSummary": summary,
        "selectedOperatingRoute": route,
        "selectedActionFamily": family,
        "actionIntent": _text(supplied.get("actionIntent") or raw.get("actionIntent")) or None,
        "preconditions": _arr(supplied.get("preconditions") or raw.get("preconditions")),
        "riskBoundaries": _arr(supplied.get("riskBoundaries") or raw.get("riskBoundaries")),
        "missingEvidence": _arr(supplied.get("missingEvidence") or raw.get("missingEvidence")),
        "selectedHypothesisId": _text(
            supplied.get("selectedHypothesisId")
            or diagnosis.get("selectedHypothesisId")
            or raw.get("selectedHypothesisId")
        )
        or None,
        "authority": "agent2_primary_judgment_source",
        "locked": decision_type == "act",
    }


def _known_agent1_fields() -> set[str]:
    return {
        "correlationId",
        "productId",
        "storeId",
        "signalId",
        "metricCode",
        "severity",
        "confidence",
        "decisionHint",
        "decisionType",
        "finding",
        "coreProblem",
        "facts",
        "evidence",
        "causalHypotheses",
        "rejectedHypotheses",
        "decisionSummary",
        "alternatives",
        "selectedOperatingRoute",
        "selectedActionFamily",
        "selectedActionFamilyHint",
        "actionIntent",
        "preconditions",
        "riskBoundaries",
        "missingEvidence",
        "excludedActions",
        "requiredActionData",
        "capacityConstraints",
        "companyHooks",
        "ragProof",
        "routeLock",
        "actionFamilyLock",
        "primaryBusinessSignal",
        "primaryOperatingGap",
        "businessHypothesis",
        "decisionCore",
        "diagnosis",
        "diagnosticExtensions",
        "diagnosticNarrative",
    }


def _normalize_extension(
    value: Dict[str, Any],
    index: int,
    *,
    family: str | None,
    decision_type: str,
    source: str,
) -> Dict[str, Any] | None:
    summary = _text(
        value.get("summary")
        or value.get("statement")
        or value.get("insight")
        or value.get("finding")
    )
    reasoning = _text(
        value.get("reasoning")
        or value.get("analysis")
        or value.get("detail")
        or value.get("description")
    )
    if not summary and reasoning:
        summary = reasoning[:240]
    if not summary:
        return None

    extension_id = _text(value.get("extensionId") or value.get("id")) or f"EXT-{index + 1:02d}"
    extension_type = _text(value.get("type") or value.get("extensionType")) or "novel_business_hypothesis"
    suggested_family = _text(
        value.get("suggestedActionFamily")
        or value.get("actionFamily")
    ) or None
    conflict = bool(suggested_family and family and suggested_family != family)
    observation_only = decision_type == "observe"
    usable = not conflict and not observation_only
    return {
        "extensionId": extension_id,
        "type": extension_type,
        "summary": summary,
        "reasoning": reasoning,
        "supportFactRefs": [
            _text(item)
            for item in _arr(value.get("supportFactRefs") or value.get("factRefs"))
            if _text(item)
        ][:16],
        "opposingFactRefs": [
            _text(item)
            for item in _arr(value.get("opposingFactRefs"))
            if _text(item)
        ][:12],
        "confidence": _confidence(value.get("confidence")),
        "impact": _text(
            value.get("impact")
            or value.get("executionImpact")
            or value.get("agent2Guidance")
        ),
        "suggestedActionFamily": suggested_family,
        "authority": "context_only",
        "source": source,
        "usableByAgent2": usable,
        "validationStatus": (
            "observation_only"
            if observation_only
            else "conflict_with_action_family_lock"
            if conflict
            else "context_ready"
        ),
        "conflictReason": (
            f"extension suggested {suggested_family}, but Agent1 locked {family}"
            if conflict
            else None
        ),
    }


def _extensions(
    raw: Dict[str, Any],
    core: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], List[str]]:
    values: List[tuple[Dict[str, Any], str]] = []
    for item in _arr(raw.get("diagnosticExtensions"))[:MAX_DIAGNOSTIC_EXTENSIONS]:
        if isinstance(item, dict):
            values.append((item, "agent_declared_extension"))

    unmapped_names: List[str] = []
    known = _known_agent1_fields()
    for key, value in raw.items():
        if key in known or value in (None, "", [], {}):
            continue
        unmapped_names.append(str(key))
        if len(values) >= MAX_DIAGNOSTIC_EXTENSIONS:
            continue
        if isinstance(value, dict):
            extension_value = {
                **value,
                "type": value.get("type") or f"agent_defined.{key}",
                "summary": value.get("summary") or f"{key}: {_json_text(value, 360)}",
            }
        else:
            extension_value = {
                "type": f"agent_defined.{key}",
                "summary": f"{key}: {_json_text(value, 360)}",
                "reasoning": _json_text(value),
            }
        values.append((extension_value, "unmapped_top_level_field"))

    normalized: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, (value, source) in enumerate(values[:MAX_DIAGNOSTIC_EXTENSIONS]):
        item = _normalize_extension(
            value,
            index,
            family=core.get("selectedActionFamily"),
            decision_type=str(core.get("decisionType")),
            source=source,
        )
        if not item:
            continue
        extension_id = str(item["extensionId"])
        if extension_id in seen_ids:
            extension_id = f"{extension_id}-{index + 1}"
            item["extensionId"] = extension_id
        seen_ids.add(extension_id)
        normalized.append(item)
    return normalized, unmapped_names


def _dual_channel_metadata(
    raw: Dict[str, Any],
    allowed_families: set[str],
) -> Dict[str, Any]:
    core = _decision_core(raw, allowed_families)
    diagnosis = _dict(raw.get("diagnosis"))
    extensions, unmapped = _extensions(raw, core)
    narrative = _text(
        raw.get("diagnosticNarrative")
        or diagnosis.get("diagnosticNarrative")
        or raw.get("businessHypothesis")
        or raw.get("finding"),
        1800,
    )
    usable = [item for item in extensions if item.get("usableByAgent2") is True]
    conflicts = [
        item
        for item in extensions
        if item.get("validationStatus") == "conflict_with_action_family_lock"
    ]
    report = {
        "version": VERSION,
        "status": "passed",
        "fixedDecisionCorePassed": True,
        "decisionCoreAuthority": "agent2_primary_judgment_source",
        "extensionAuthority": "context_only",
        "extensionCount": len(extensions),
        "usableExtensionCount": len(usable),
        "conflictingExtensionCount": len(conflicts),
        "unmappedTopLevelFieldNames": unmapped[:20],
        "rawOutputFingerprint": _fingerprint(raw),
        "rule": (
            "The fixed core decides what Agent2 may do. Extensions only enrich "
            "the current execution context and cannot override locks or limits."
        ),
    }
    return {
        "decisionCore": core,
        "diagnosis": {
            "coreProblem": core.get("coreProblem"),
            "facts": _arr(diagnosis.get("facts") or raw.get("facts") or raw.get("evidence")),
            "causalHypotheses": _arr(
                diagnosis.get("causalHypotheses") or raw.get("causalHypotheses")
            ),
            "selectedHypothesisId": core.get("selectedHypothesisId"),
            "rejectedHypotheses": _arr(
                diagnosis.get("rejectedHypotheses") or raw.get("rejectedHypotheses")
            ),
            "alternatives": _arr(diagnosis.get("alternatives") or raw.get("alternatives")),
        },
        "diagnosticExtensions": extensions,
        "diagnosticNarrative": narrative,
        "validationReport": report,
    }


def _mapped_legacy_row(raw: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    core = _dict(metadata.get("decisionCore"))
    diagnosis = _dict(metadata.get("diagnosis"))
    mapped = dict(raw)
    mapped.update(
        decisionType=core.get("decisionType"),
        confidence=core.get("confidence"),
        coreProblem=core.get("coreProblem"),
        decisionSummary=core.get("decisionSummary"),
        selectedOperatingRoute=core.get("selectedOperatingRoute"),
        selectedActionFamilyHint=core.get("selectedActionFamily"),
        actionIntent=core.get("actionIntent"),
        preconditions=core.get("preconditions"),
        riskBoundaries=core.get("riskBoundaries"),
        missingEvidence=core.get("missingEvidence"),
        facts=diagnosis.get("facts"),
        causalHypotheses=diagnosis.get("causalHypotheses"),
        rejectedHypotheses=diagnosis.get("rejectedHypotheses"),
        alternatives=diagnosis.get("alternatives"),
    )
    return mapped


def _augment_agent1_prompt(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    result = [dict(item) for item in messages]
    if not result:
        return result
    result[0]["content"] = str(result[0].get("content") or "") + (
        "\nV22双通道输出合同：每项必须输出decisionCore、diagnosis、diagnosticExtensions、"
        "diagnosticNarrative。decisionCore是Agent2唯一权威主判断源，必须包含decisionType、"
        "coreProblem、decisionSummary、selectedOperatingRoute、selectedActionFamily、actionIntent、"
        "preconditions、riskBoundaries、missingEvidence、confidence。不得遗漏decisionType后让系统猜测。"
        "observe时selectedActionFamily必须为null且route为observe；act时必须选择一个合法动作族。"
        "diagnosticExtensions是自由经营洞察数组，字段为extensionId、type、summary、reasoning、"
        "supportFactRefs、opposingFactRefs、confidence、impact、suggestedActionFamily。自由洞察只能增强"
        "本次任务上下文，不能覆盖decisionCore、动作族锁、权限或数字边界。新的非标准洞察优先写入"
        "diagnosticExtensions，不要通过改变固定字段名表达。"
    )
    return result


def _build_agent1_messages(
    data_version: str | None,
    batch: List[Dict[str, Any]],
    rag_context: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    messages, payload = _ORIGINALS["agent1_build_messages"](
        data_version,
        batch,
        rag_context,
    )
    payload = dict(payload)
    payload["outputContract"] = {
        "version": VERSION,
        "mode": "fixed_decision_core_plus_context_extensions",
        "decisionCoreAuthority": "agent2_primary_judgment_source",
        "diagnosticExtensionsAuthority": "context_only",
    }
    return _augment_agent1_prompt(messages), payload


def _normalize_agent1_judgments(
    provider_payload: Dict[str, Any],
    source_maps: Dict[str, Any],
    data_version: str | None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from src.services import real_product_judgment_agent_v196_service as agent1

    raw_items = provider_payload.get("judgments") if isinstance(provider_payload, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("provider_json_missing_judgments_array")

    filtered: List[Dict[str, Any]] = []
    metadata_by_key: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    contract_errors: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            contract_errors.append({"reason": "judgment_not_object"})
            continue
        try:
            metadata = _dual_channel_metadata(raw, set(agent1.ALLOWED_ACTION_FAMILIES))
        except ValueError as exc:
            contract_errors.append(
                {
                    "reason": str(exc),
                    "correlationId": raw.get("correlationId"),
                    "productId": raw.get("productId"),
                    "storeId": raw.get("storeId"),
                }
            )
            continue
        mapped = _mapped_legacy_row(raw, metadata)
        filtered.append(mapped)
        metadata_by_key[_identity_key(mapped)] = metadata

    normalized, diagnostics = _ORIGINALS["agent1_normalize_judgments"](
        {"judgments": filtered},
        source_maps,
        data_version,
    )
    for item in normalized:
        metadata = metadata_by_key.get(_identity_key(item))
        if not metadata:
            metadata = next(
                (
                    value
                    for key, value in metadata_by_key.items()
                    if key[1] == _text(item.get("storeId"))
                    and key[2] == _text(item.get("productId"))
                ),
                None,
            )
        if not metadata:
            continue
        decision_core = _dict(metadata.get("decisionCore"))
        diagnosis = _dict(metadata.get("diagnosis"))
        extensions = _arr(metadata.get("diagnosticExtensions"))
        narrative = _text(metadata.get("diagnosticNarrative"))
        validation = _dict(metadata.get("validationReport"))

        ir = _dict(item.get("agent1DecisionIR"))
        ir.update(
            decisionCore=decision_core,
            diagnosis=diagnosis,
            diagnosticExtensions=extensions,
            diagnosticNarrative=narrative,
            extensionValidation=validation,
            outputMode="fixed_core_plus_context_extensions",
        )
        item["agent1DecisionIR"] = ir

        judgment = _dict(item.get("agent1OperatingJudgment"))
        judgment.update(
            decisionCore=decision_core,
            diagnosis=diagnosis,
            diagnosticExtensions=extensions,
            diagnosticNarrative=narrative,
            extensionValidation=validation,
            agent1DecisionIR=ir,
        )
        item["agent1OperatingJudgment"] = judgment
        item["agent1DecisionCore"] = decision_core
        item["agent1DiagnosticExtensions"] = extensions
        item["agent1DiagnosticNarrative"] = narrative
        item["agent1ValidationReport"] = validation

    diagnostics = dict(diagnostics)
    diagnostics.update(
        dualChannelContractVersion=VERSION,
        fixedDecisionCoreCount=len(normalized),
        invalidDualChannelContractCount=len(contract_errors),
        dualChannelContractErrors=contract_errors[:20],
        decisionCoreAuthority="agent2_primary_judgment_source",
        diagnosticExtensionsAuthority="context_only",
    )
    diagnostics["invalidProviderContractCount"] = int(
        diagnostics.get("invalidProviderContractCount") or 0
    ) + len(contract_errors)
    return normalized, diagnostics


def _strict_contract_normalize_agent1(
    raw: Dict[str, Any],
    family: Any = None,
    route: Any = None,
) -> Dict[str, Any]:
    source = _dict(raw.get("agent1OperatingJudgment")) or raw
    ir = _dict(raw.get("agent1DecisionIR")) or _dict(source.get("agent1DecisionIR"))
    core = _dict(ir.get("decisionCore")) or _dict(source.get("decisionCore"))
    decision_type = _text(
        core.get("decisionType")
        or source.get("decisionType")
        or raw.get("decisionType")
    ).lower()
    if decision_type not in {"act", "observe"}:
        raise ValueError("agent1_decision_type_missing_or_invalid")
    mapped = dict(raw)
    mapped_source = dict(source)
    mapped_source.update(
        decisionType=decision_type,
        selectedOperatingRoute=(
            "observe"
            if decision_type == "observe"
            else core.get("selectedOperatingRoute")
            or source.get("selectedOperatingRoute")
        ),
        selectedActionFamily=(
            None
            if decision_type == "observe"
            else core.get("selectedActionFamily")
            or source.get("selectedActionFamily")
        ),
    )
    mapped["agent1OperatingJudgment"] = mapped_source
    return _ORIGINALS["contract_normalize_agent1"](mapped, family, route)


def _decision_ir(package: Dict[str, Any]) -> Dict[str, Any]:
    ir = _dict(package.get("agent1DecisionIR"))
    if ir:
        return ir
    return _dict(_dict(package.get("agent1OperatingJudgment")).get("agent1DecisionIR"))


def _usable_extensions(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    ir = _decision_ir(package)
    return [
        item
        for item in _arr(ir.get("diagnosticExtensions"))
        if isinstance(item, dict) and item.get("usableByAgent2") is True
    ][:MAX_AGENT2_EXTENSIONS]


def _compact_agent2_package(package: Dict[str, Any]) -> Dict[str, Any]:
    base = dict(_ORIGINALS["agent2_compact_package"](package))
    ir = _decision_ir(package)
    core = _dict(ir.get("decisionCore"))
    if not core:
        core = {
            "version": VERSION,
            "decisionType": ir.get("decisionType"),
            "confidence": package.get("confidence"),
            "coreProblem": ir.get("coreProblem"),
            "decisionSummary": ir.get("decisionSummary"),
            "selectedOperatingRoute": package.get("selectedOperatingRoute"),
            "selectedActionFamily": package.get("actionFamily"),
            "actionIntent": ir.get("actionIntent"),
            "preconditions": ir.get("preconditions") or [],
            "riskBoundaries": ir.get("riskBoundaries") or [],
            "missingEvidence": ir.get("missingEvidence") or [],
            "authority": "agent2_primary_judgment_source",
        }
    diagnosis = _dict(ir.get("diagnosis")) or {
        "coreProblem": ir.get("coreProblem"),
        "facts": ir.get("facts") or [],
        "causalHypotheses": ir.get("causalHypotheses") or [],
        "rejectedHypotheses": ir.get("rejectedHypotheses") or [],
        "alternatives": ir.get("alternatives") or [],
    }
    extensions = _usable_extensions(package)
    extension_validation = _dict(ir.get("extensionValidation"))
    base.pop("agent1OperatingJudgment", None)
    base.update(
        agent1DecisionCore=core,
        agent1Diagnosis=diagnosis,
        agent1DecisionIR={
            "version": ir.get("version") or VERSION,
            "decisionCore": core,
            "diagnosis": diagnosis,
        },
        diagnosticExtensions=extensions,
        diagnosticNarrative=_text(ir.get("diagnosticNarrative"), 1800),
        diagnosticExtensionContract={
            "version": VERSION,
            "authority": "context_only",
            "availableExtensionIds": [item.get("extensionId") for item in extensions],
            "availableCount": len(extensions),
            "validation": extension_validation,
            "cannotOverride": [
                "lockedActionFamily",
                "agent1DecisionCore",
                "capabilityPack.permissionBounds",
                "capabilityPack.numericLimits",
                "systemFacts",
            ],
        },
    )
    return base


def _build_agent2_messages(
    data_version: str | None,
    packages: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    messages, payload = _ORIGINALS["agent2_build_messages"](
        data_version,
        packages,
    )
    result = [dict(item) for item in messages]
    if result:
        result[0]["content"] = str(result[0].get("content") or "") + (
            "\n输入优先级固定为：系统事实和权限 > agent1DecisionCore > capabilityPack > "
            "diagnosticExtensions > 执行RAG。agent1DecisionCore是主判断源，diagnosticExtensions"
            "只用于增加本次方案的商品差异、时段、计划、复盘拆分等细节，不得改变动作族、路线、"
            "权限、数字边界或被Agent1排除的方向。对每个可用扩展必须决定使用或拒绝，并输出"
            "usedDiagnosticExtensionIds、rejectedDiagnosticExtensionIds、"
            "diagnosticExtensionApplicationReason；两组ID必须覆盖全部availableExtensionIds且不能重叠。"
        )
    return result, payload


def _normalize_agent2_plan(
    raw: Dict[str, Any],
    package: Dict[str, Any],
    proof: Dict[str, Any],
) -> Dict[str, Any]:
    plan = dict(_ORIGINALS["agent2_normalize_plan"](raw, package, proof))
    extensions = _usable_extensions(package)
    available_ids = {
        _text(item.get("extensionId"))
        for item in extensions
        if _text(item.get("extensionId"))
    }
    used = {
        _text(item)
        for item in _arr(raw.get("usedDiagnosticExtensionIds"))
        if _text(item)
    }
    rejected = {
        _text(item)
        for item in _arr(raw.get("rejectedDiagnosticExtensionIds"))
        if _text(item)
    }
    reason = _text(raw.get("diagnosticExtensionApplicationReason"))
    failures: List[str] = []
    if available_ids:
        if not reason:
            failures.append("diagnosticExtensionApplicationReason")
        if used & rejected:
            failures.append("diagnostic_extension_id_cannot_be_used_and_rejected")
        if (used | rejected) != available_ids:
            failures.append("diagnostic_extension_audit_must_cover_available_ids")
        if not used.issubset(available_ids) or not rejected.issubset(available_ids):
            failures.append("diagnostic_extension_ids_must_be_available")

    trace = {
        "version": VERSION,
        "authority": "context_only",
        "availableExtensionIds": sorted(available_ids),
        "usedExtensionIds": sorted(used),
        "rejectedExtensionIds": sorted(rejected),
        "applicationReason": reason,
        "validationStatus": "passed" if not failures else "failed",
        "failures": failures,
        "cannotOverrideActionFamily": True,
    }
    plan["usedDiagnosticExtensionIds"] = sorted(used)
    plan["rejectedDiagnosticExtensionIds"] = sorted(rejected)
    plan["diagnosticExtensionApplicationReason"] = reason
    plan["diagnosticExtensionTrace"] = trace
    plan["decisionCoreAuthority"] = "agent2_primary_judgment_source"
    plan["diagnosticExtensionsAuthority"] = "context_only"

    missing = list(plan.get("semanticContractMissing") or [])
    missing.extend(failures)
    plan["semanticContractMissing"] = list(dict.fromkeys(missing))
    if plan.get("actionPlanStatus") == "ready" and failures:
        plan["actionPlanStatus"] = "action_plan_missing_data"
        plan["conflictReason"] = (
            "Agent2 did not complete the diagnostic-extension audit: "
            + ",".join(failures)
        )
        plan["reason"] = plan["conflictReason"]
    active = _dict(plan.get("activeActionContract"))
    active["diagnosticExtensionTrace"] = trace
    plan["activeActionContract"] = active
    return plan


def bind_agent1_dual_channel_contract() -> None:
    """Bind the dual-channel contract into the existing single V22 runtime."""
    global _BOUND
    if _BOUND:
        return

    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_runtime_contract_v2010_service as contract
    from src.services import real_product_judgment_agent_v196_service as agent1

    _ORIGINALS.update(
        agent1_build_messages=agent1._build_messages,
        agent1_normalize_judgments=agent1._normalize_judgments,
        contract_normalize_agent1=contract.normalize_agent1_judgment,
        agent2_compact_package=agent2._compact_package,
        agent2_build_messages=agent2._build_messages,
        agent2_normalize_plan=agent2._normalize_plan,
    )

    agent1._build_messages = _build_agent1_messages
    agent1._normalize_judgments = _normalize_agent1_judgments
    contract.normalize_agent1_judgment = _strict_contract_normalize_agent1
    agent2._compact_package = _compact_agent2_package
    agent2._build_messages = _build_agent2_messages
    agent2._normalize_plan = _normalize_agent2_plan

    agent1.AGENT1_DUAL_CHANNEL_CONTRACT_VERSION = VERSION
    agent2.AGENT1_DUAL_CHANNEL_CONTRACT_VERSION = VERSION
    contract.AGENT1_DUAL_CHANNEL_CONTRACT_VERSION = VERSION
    _BOUND = True


__all__ = [
    "AGENT1_DUAL_CHANNEL_CONTRACT_VERSION",
    "bind_agent1_dual_channel_contract",
]
