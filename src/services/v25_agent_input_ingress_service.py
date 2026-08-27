"""V25.7-V25.9 artifact-level Agent knowledge ingress authority.

The V25 knowledge migration is complete only when immutable ``agent1InputRef`` and
``agent2InputRef`` artifacts carry the registered knowledge envelope.  This module
extends the existing strict input contracts after all V22/V22.5 binders are active;
it does not create a second Agent runtime or bypass Artifact Hub.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict, List

V25_KNOWLEDGE_INGRESS_VERSION = "25.9.0"
_INSTALLED = False
_ORIGINALS: Dict[str, Any] = {}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _knowledge_module() -> Any:
    from src.services import unified_agent_knowledge_v25_service as knowledge

    return knowledge


def _float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace("%", "").strip())
    except Exception:
        return None


def _agent1_composition_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    metric_codes: List[str] = []
    signal_flags: List[str] = []
    signals = _arr(_dict(payload.get("snapshotLayer")).get("fieldSignals"))
    for item in signals:
        if not isinstance(item, dict):
            continue
        raw_code = _text(
            item.get("metricCode")
            or item.get("metricName")
            or item.get("code")
        )
        code = raw_code.lower()
        if code and code not in metric_codes:
            metric_codes.append(code)
        if "ctr" in code or "点击率" in raw_code:
            if "ctr" not in metric_codes:
                metric_codes.append("ctr")
        organic = any(
            marker in code
            for marker in (
                "organic",
                "natural",
                "organicvisitors",
                "organictraffic",
            )
        ) or any(marker in raw_code for marker in ("自然流量", "自然访客", "自然搜索"))
        delta = _float(
            item.get("changeRatio")
            if item.get("changeRatio") is not None
            else item.get("changeRate")
            if item.get("changeRate") is not None
            else item.get("deltaRate")
        )
        direction = _text(item.get("direction") or item.get("trendDirection")).lower()
        declining = (delta is not None and delta < 0) or direction in {
            "down",
            "decline",
            "decreasing",
            "下降",
            "下滑",
        }
        if organic and declining and "organic_traffic_decline" not in signal_flags:
            signal_flags.append("organic_traffic_decline")
    cross_metric_required = bool(
        len(signals) >= 2
        or payload.get("strongRelations")
        or payload.get("crossValidation")
    )
    return {
        "metricCodes": metric_codes,
        "signalFlags": signal_flags,
        "crossMetricRequired": cross_metric_required,
    }


def _runtime_guardrails_agent1(value: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "version": value.get("version"),
        "mode": value.get("mode"),
        "principles": deepcopy(value.get("principles") or []),
        "guardrails": deepcopy(value.get("guardrails") or {}),
        "knowledgeAuthority": "V25_UNIFIED_KNOWLEDGE",
        "legacyFieldRole": "RUNTIME_GUARDRAILS_ONLY",
        "legacyDirectKnowledgeReadAllowed": False,
        "mayCreateSystemFact": False,
    }
    return {key: child for key, child in result.items() if child not in (None, "", [], {})}


def _runtime_guardrails_agent2(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "version": V25_KNOWLEDGE_INGRESS_VERSION,
        "lockedActionFamily": payload.get("lockedActionFamily") or payload.get("actionFamily"),
        "knowledgeAuthority": "V25_UNIFIED_KNOWLEDGE",
        "legacyRagContextRole": "AUDIT_METADATA_ONLY",
        "legacyDirectKnowledgeReadAllowed": False,
        "taskGate": False,
        "mayCreateSystemFact": False,
    }


def _agent1_knowledge(payload: Dict[str, Any], legacy_context: Dict[str, Any]) -> Dict[str, Any]:
    knowledge = _knowledge_module()
    plan = knowledge.compose_knowledge_plan(
        "Agent1",
        _agent1_composition_context(payload),
    )
    dropped = len(_arr(legacy_context.get("experienceCards")))
    gaps = [
        {
            "canonicalField": item.get("canonicalField"),
            "fieldHash": item.get("fieldHash"),
            "role": item.get("role"),
            "reason": "NO_FORMAL_V25_KNOWLEDGE_RECORD_AVAILABLE",
        }
        for item in _arr(plan.get("fields"))
        if isinstance(item, dict)
    ]
    envelope = {
        "schema": "rag.agent_knowledge_envelope.v1",
        "version": V25_KNOWLEDGE_INGRESS_VERSION,
        "agent": "Agent1",
        "composition": plan,
        "formalKnowledgeItems": [],
        "compatibilitySupplement": [],
        "insufficientEvidence": gaps,
        "legacyDirectExperienceDroppedCount": dropped,
        "legacyDirectKnowledgeReadAllowed": False,
        "mayCreateSystemFact": False,
    }
    envelope["envelopeHash"] = _canonical_hash(envelope)
    return envelope


def _agent2_knowledge(payload: Dict[str, Any], legacy_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    knowledge = _knowledge_module()
    return knowledge.build_agent2_unified_knowledge_context(payload, legacy_snapshot)


def _sanitize_agent2_rag_audit(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    keep = (
        "version",
        "status",
        "queryFingerprint",
        "matchedCount",
        "approvedCaseIds",
        "retrievalSource",
        "retrievalExecuted",
        "retrievalCount",
        "retrievalCountThisCall",
        "demoSeedExcluded",
        "minimumQuality",
        "taskGate",
        "emptyResultAllowed",
    )
    result = {
        key: deepcopy(snapshot.get(key))
        for key in keep
        if snapshot.get(key) not in (None, "", [], {})
    }
    result.update(
        knowledgeIngress="V25_UNIFIED_KNOWLEDGE",
        legacyKnowledgePayloadRemoved=True,
        legacyDirectKnowledgeReadAllowed=False,
    )
    return result


def _seal_input_contract(
    payload: Dict[str, Any],
    *,
    agent: str,
    knowledge: Dict[str, Any],
) -> None:
    contract = dict(_dict(payload.get("inputContract")))
    composition = _dict(knowledge.get("composition"))
    contract.update(
        knowledgeIngressVersion=V25_KNOWLEDGE_INGRESS_VERSION,
        knowledgeIngress="V25_UNIFIED_KNOWLEDGE",
        knowledgeAgent=agent,
        knowledgeEnvelopeHash=knowledge.get("envelopeHash"),
        knowledgeCompositionHash=composition.get("compositionHash"),
        legacyDirectKnowledgeReadAllowed=False,
        knowledgeMayCreateSystemFact=False,
    )
    payload["inputContract"] = contract


def _augment_agent1_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(payload)
    legacy = _dict(result.get("diagnosticRag"))
    guardrails = _runtime_guardrails_agent1(legacy)
    knowledge = _agent1_knowledge(result, legacy)
    result["runtimeGuardrails"] = guardrails
    # Kept only because the existing Agent1 prompt builder accepts this parameter.
    # It contains no RAG records after V25 and is contractually runtime guardrails.
    result["diagnosticRag"] = deepcopy(guardrails)
    result["unifiedKnowledge"] = knowledge
    _seal_input_contract(result, agent="Agent1", knowledge=knowledge)
    return result


def _augment_agent2_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(payload)
    legacy_snapshot = _dict(result.get("ragContextSnapshot"))
    knowledge = _agent2_knowledge(result, legacy_snapshot)
    result["runtimeGuardrails"] = _runtime_guardrails_agent2(result)
    result["ragContextSnapshot"] = _sanitize_agent2_rag_audit(legacy_snapshot)
    result["unifiedKnowledge"] = knowledge
    _seal_input_contract(result, agent="Agent2", knowledge=knowledge)
    return result


def _augment_payload(schema: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if str(schema).startswith("agent_input.agent1."):
        return _augment_agent1_payload(payload)
    if str(schema).startswith("agent_input.agent2."):
        return _augment_agent2_payload(payload)
    return payload


def _knowledge_errors(value: Any) -> List[str]:
    if not isinstance(value, dict):
        return []
    schema = _text(value.get("schema"))
    if not (
        schema.startswith("agent_input.agent1.")
        or schema.startswith("agent_input.agent2.")
    ):
        return []
    payload = _dict(value.get("payload"))
    contract = _dict(payload.get("inputContract"))
    knowledge = _dict(payload.get("unifiedKnowledge"))
    guardrails = _dict(payload.get("runtimeGuardrails"))
    expected_agent = "Agent1" if schema.startswith("agent_input.agent1.") else "Agent2"
    errors: List[str] = []
    if contract.get("knowledgeIngressVersion") != V25_KNOWLEDGE_INGRESS_VERSION:
        errors.append("knowledge_ingress_version_missing")
    if contract.get("legacyDirectKnowledgeReadAllowed") is not False:
        errors.append("legacy_direct_knowledge_read_not_revoked")
    if not guardrails:
        errors.append("runtime_guardrails_missing")
    if not knowledge:
        errors.append("unified_knowledge_missing")
        return errors
    if knowledge.get("schema") != "rag.agent_knowledge_envelope.v1":
        errors.append("unified_knowledge_schema_invalid")
    if knowledge.get("version") != V25_KNOWLEDGE_INGRESS_VERSION:
        errors.append("unified_knowledge_version_invalid")
    if knowledge.get("agent") != expected_agent:
        errors.append("unified_knowledge_agent_mismatch")
    if knowledge.get("mayCreateSystemFact") is not False:
        errors.append("unified_knowledge_may_create_system_fact")
    if knowledge.get("legacyDirectKnowledgeReadAllowed") is not False:
        errors.append("unified_knowledge_legacy_direct_read_not_revoked")
    declared_envelope_hash = _text(knowledge.get("envelopeHash"))
    material = dict(knowledge)
    material.pop("envelopeHash", None)
    if declared_envelope_hash != _canonical_hash(material):
        errors.append("unified_knowledge_envelope_hash_mismatch")
    composition = _dict(knowledge.get("composition"))
    declared_composition_hash = _text(composition.get("compositionHash"))
    composition_material = dict(composition)
    composition_material.pop("compositionHash", None)
    if not declared_composition_hash or declared_composition_hash != _canonical_hash(composition_material):
        errors.append("unified_knowledge_composition_hash_mismatch")
    if _text(contract.get("knowledgeEnvelopeHash")) != declared_envelope_hash:
        errors.append("input_contract_knowledge_envelope_hash_mismatch")
    if _text(contract.get("knowledgeCompositionHash")) != declared_composition_hash:
        errors.append("input_contract_knowledge_composition_hash_mismatch")
    if expected_agent == "Agent1":
        if _arr(_dict(payload.get("diagnosticRag")).get("experienceCards")):
            errors.append("agent1_legacy_experience_cards_present")
    else:
        legacy = _dict(payload.get("ragContextSnapshot"))
        for key in ("positiveExperienceCards", "negativeCases", "agentInstruction"):
            if legacy.get(key) not in (None, "", [], {}):
                errors.append("agent2_legacy_rag_payload_present:" + key)
    return errors


def _wrap_validator(original: Any) -> Any:
    def validate(value: Any, *, expected_schema: str | None = None) -> Dict[str, Any]:
        result = dict(original(value, expected_schema=expected_schema))
        errors = list(result.get("errors") or [])
        errors.extend(_knowledge_errors(value))
        result["errors"] = list(dict.fromkeys(errors))
        result["ok"] = not result["errors"]
        result["knowledgeIngressVersion"] = V25_KNOWLEDGE_INGRESS_VERSION
        return result

    return validate


def _wrap_projection_builder(original: Any) -> Any:
    def build(
        *,
        schema: str,
        payload: Dict[str, Any],
        source_artifact_refs: Any,
        source_content_hash: str,
    ) -> Dict[str, Any]:
        return original(
            schema=schema,
            payload=_augment_payload(schema, payload),
            source_artifact_refs=source_artifact_refs,
            source_content_hash=source_content_hash,
        )

    return build


def _rewrite_user_payload(messages: List[Dict[str, str]], payload: Dict[str, Any]) -> List[Dict[str, str]]:
    result = [dict(item) for item in messages]
    for index in range(len(result) - 1, -1, -1):
        if result[index].get("role") == "user":
            result[index]["content"] = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            break
    return result


def _augment_system_prompt(messages: List[Dict[str, str]], instruction: str) -> List[Dict[str, str]]:
    result = [dict(item) for item in messages]
    if result and result[0].get("role") == "system":
        result[0]["content"] = _text(result[0].get("content")) + "\n" + instruction
    return result


def _agent1_messages_v25(
    data_version: str | None,
    batch: List[Dict[str, Any]],
    rag_context: Dict[str, Any],
) -> tuple[List[Dict[str, str]], Dict[str, Any]]:
    del rag_context
    if not batch:
        return _ORIGINALS["agent1_build_messages"](data_version, batch, {})
    guardrails = _dict(batch[0].get("runtimeGuardrails"))
    messages, payload = _ORIGINALS["agent1_build_messages"](
        data_version,
        batch,
        guardrails,
    )
    payload = dict(payload)
    payload["knowledgeIngressVersion"] = V25_KNOWLEDGE_INGRESS_VERSION
    payload["unifiedKnowledgeByItem"] = [
        {
            "correlationId": item.get("correlationId"),
            "productId": item.get("productId"),
            "storeId": item.get("storeId"),
            "knowledge": deepcopy(_dict(item.get("unifiedKnowledge"))),
        }
        for item in batch
    ]
    messages = _rewrite_user_payload(messages, payload)
    messages = _augment_system_prompt(
        messages,
        "V25知识合同：知识只能来自每个商品的unifiedKnowledge；runtimeGuardrails仅约束行为，"
        "不是知识库。insufficientEvidence必须保持缺口，不得由模型补成事实；任何知识项都不能创建"
        "系统事实、权限或状态。",
    )
    return messages, payload


def _agent2_compact_v25(package: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(_ORIGINALS["agent2_compact_package"](package))
    formal = _dict(package.get("unifiedKnowledge"))
    if formal:
        result["unifiedKnowledge"] = deepcopy(formal)
    result["runtimeGuardrails"] = deepcopy(_dict(package.get("runtimeGuardrails")))
    result.pop("ragContext", None)
    result["legacyDirectRagContextRemoved"] = True
    result["knowledgeIngressVersion"] = V25_KNOWLEDGE_INGRESS_VERSION
    return result


def _agent2_messages_v25(
    data_version: str | None,
    packages: List[Dict[str, Any]],
) -> tuple[List[Dict[str, str]], Dict[str, Any]]:
    messages, payload = _ORIGINALS["agent2_build_messages"](data_version, packages)
    payload = dict(payload)
    payload["knowledgeIngressVersion"] = V25_KNOWLEDGE_INGRESS_VERSION
    messages = _rewrite_user_payload(messages, payload)
    messages = _augment_system_prompt(
        messages,
        "V25知识合同：执行知识只允许读取package.unifiedKnowledge。ragContextSnapshot只保留审计元数据，"
        "不得作为知识内容读取。compatibilitySupplement仅为经统一组合表授权后的兼容补充，不能覆盖"
        "当前事实、Agent1动作族锁、权限或数字边界，也不能生成系统事实。",
    )
    return messages, payload


def install_v25_agent_input_ingress() -> None:
    """Make V25 knowledge mandatory at immutable Agent-input Artifact boundaries."""
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_input_contract_v2258_service as contract2258
    from src.services import agent_input_contract_v230_service as contract230
    from src.services import agent_input_transport_v2258_service as transport2258
    from src.services import agent_input_transport_v230_service as transport230
    from src.services import real_product_judgment_agent_v196_service as agent1

    # Explicitly extend the strict DTO allowlists.  Unknown fields remain blocked.
    contract230._AGENT1_PAYLOAD_KEYS.update({"runtimeGuardrails", "unifiedKnowledge"})
    contract230._AGENT2_PAYLOAD_KEYS.update({"runtimeGuardrails", "unifiedKnowledge"})
    contract2258._AGENT1_PAYLOAD_KEYS.update({"runtimeGuardrails", "unifiedKnowledge"})

    _ORIGINALS.update(
        contract230_validate=contract230.validate_agent_input_envelope,
        contract2258_validate=contract2258.validate_agent_input_envelope,
        contract230_build=contract230.build_projection_envelope,
        contract2258_build=contract2258.build_projection_envelope,
        agent1_build_messages=agent1._build_messages,
        agent2_compact_package=agent2._compact_package,
        agent2_build_messages=agent2._build_messages,
    )

    validate230 = _wrap_validator(_ORIGINALS["contract230_validate"])
    validate2258 = _wrap_validator(_ORIGINALS["contract2258_validate"])
    build230 = _wrap_projection_builder(_ORIGINALS["contract230_build"])
    build2258 = _wrap_projection_builder(_ORIGINALS["contract2258_build"])

    # Existing assert_* function objects read validate_* through module globals, so
    # replacing validators invalidates pre-V25 input artifacts and forces REBUILD.
    contract230.validate_agent_input_envelope = validate230
    contract2258.validate_agent_input_envelope = validate2258

    # Transport modules imported the builder functions by value. Patch both the
    # contract modules and those imported globals so every new Artifact is sealed.
    contract230.build_projection_envelope = build230
    contract2258.build_projection_envelope = build2258
    transport230.build_projection_envelope = build230
    transport2258.build_projection_envelope = build2258

    # Final model-facing prompt builders consume the Artifact-carried envelope.
    agent1._build_messages = _agent1_messages_v25
    agent2._compact_package = _agent2_compact_v25
    agent2._build_messages = _agent2_messages_v25

    agent1.V25_KNOWLEDGE_INGRESS_VERSION = V25_KNOWLEDGE_INGRESS_VERSION
    agent2.V25_KNOWLEDGE_INGRESS_VERSION = V25_KNOWLEDGE_INGRESS_VERSION
    transport230.V25_KNOWLEDGE_INGRESS_VERSION = V25_KNOWLEDGE_INGRESS_VERSION
    transport2258.V25_KNOWLEDGE_INGRESS_VERSION = V25_KNOWLEDGE_INGRESS_VERSION
    contract230.V25_KNOWLEDGE_INGRESS_VERSION = V25_KNOWLEDGE_INGRESS_VERSION
    contract2258.V25_KNOWLEDGE_INGRESS_VERSION = V25_KNOWLEDGE_INGRESS_VERSION
    _INSTALLED = True


__all__ = [
    "V25_KNOWLEDGE_INGRESS_VERSION",
    "install_v25_agent_input_ingress",
]
