"""V25.6-V25.9 unified Agent knowledge ingress.

This module does not create a second Agent runtime. It composes registered
knowledge fields and patches only knowledge-provider references after the V22
single runtime installer has bound the production chain.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List

V25_AGENT_KNOWLEDGE_VERSION = "25.9.0"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALLED = False


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


@lru_cache(maxsize=1)
def _field_registry() -> Dict[str, Any]:
    path = _REPO_ROOT / "governance" / "v25" / "rag-field-registry-v25.json"
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _composition_table() -> Dict[str, Any]:
    path = _REPO_ROOT / "governance" / "v25" / "knowledge-composition-table-v25.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _field_index() -> Dict[str, Dict[str, Any]]:
    return {
        _text(item.get("canonicalField")): item
        for item in _arr(_field_registry().get("fields"))
        if isinstance(item, dict) and _text(item.get("canonicalField"))
    }


def _composition_for(agent: str) -> Dict[str, Any]:
    for item in _arr(_composition_table().get("compositions")):
        if isinstance(item, dict) and _text(item.get("agent")) == agent:
            return item
    raise ValueError(f"unknown_v25_knowledge_composition_agent:{agent}")


def _path_value(context: Dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _predicate_matches(predicate: Dict[str, Any], context: Dict[str, Any]) -> bool:
    op = _text(predicate.get("op"))
    path = _text(predicate.get("path"))
    actual = _path_value(context, path)
    expected = predicate.get("value")
    if op == "EQ":
        return actual == expected
    if op == "IN":
        return actual in _arr(expected)
    if op == "CONTAINS":
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        return _text(expected).lower() in _text(actual).lower()
    if op == "TRUTHY":
        return bool(actual)
    raise ValueError(f"unsupported_v25_knowledge_predicate:{op}")


def _verify_field_ref(agent: str, ref: Dict[str, Any]) -> Dict[str, Any]:
    canonical = _text(ref.get("canonicalField"))
    field = _field_index().get(canonical)
    if not field:
        raise ValueError(f"unknown_v25_knowledge_field:{canonical}")
    if _text(field.get("fieldHash")) != _text(ref.get("fieldHash")):
        raise ValueError(f"v25_knowledge_field_hash_mismatch:{canonical}")
    if agent not in [_text(value) for value in _arr(field.get("consumers"))]:
        raise ValueError(f"v25_knowledge_consumer_forbidden:{agent}:{canonical}")
    for pattern in _arr(_field_registry().get("systemContractExclusions")):
        prefix = _text(pattern)
        if prefix.endswith("*"):
            prefix = prefix[:-1]
        if prefix and canonical.startswith(prefix):
            raise ValueError(f"v25_system_contract_forbidden_in_knowledge:{canonical}")
    return field


def compose_knowledge_plan(agent: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Compile the only legal field request set for one Agent/stage."""
    context = _dict(context)
    composition = _composition_for(agent)
    selected: Dict[str, Dict[str, Any]] = {}
    reasons: Dict[str, List[str]] = {}

    def add(ref: Dict[str, Any], reason: str) -> None:
        field = _verify_field_ref(agent, ref)
        canonical = _text(field.get("canonicalField"))
        selected.setdefault(
            canonical,
            {
                "canonicalField": canonical,
                "fieldHash": field.get("fieldHash"),
                "domains": deepcopy(field.get("domains") or []),
                "preferredRetrieval": deepcopy(field.get("preferredRetrieval") or []),
                "role": _text(ref.get("role") or "REQUIRED"),
            },
        )
        reasons.setdefault(canonical, []).append(reason)

    for raw in _arr(composition.get("baseFields")):
        if isinstance(raw, dict):
            add(raw, "BASE")

    matched_groups: List[str] = []
    for raw in _arr(composition.get("conditionalGroups")):
        if not isinstance(raw, dict):
            continue
        predicate = _dict(raw.get("when"))
        if _predicate_matches(predicate, context):
            group_id = _text(raw.get("groupId"))
            matched_groups.append(group_id)
            for ref in _arr(raw.get("fields")):
                if isinstance(ref, dict):
                    add(ref, group_id)

    fields: List[Dict[str, Any]] = []
    for canonical, item in selected.items():
        projection = dict(item)
        projection["selectionReasons"] = reasons.get(canonical) or []
        fields.append(projection)

    material = {
        "schema": "rag.knowledge_composition_plan.v1",
        "version": V25_AGENT_KNOWLEDGE_VERSION,
        "compositionId": composition.get("compositionId"),
        "compositionVersion": _composition_table().get("version"),
        "agent": agent,
        "stage": composition.get("stage"),
        "matchedConditionalGroups": matched_groups,
        "fields": fields,
        "mayCreateSystemFact": False,
    }
    return {**material, "compositionHash": _canonical_hash(material)}


def _projection_record(
    field: Dict[str, Any],
    content: Dict[str, Any],
    *,
    source_ref: str | None,
    record_id: str,
) -> Dict[str, Any]:
    return {
        "recordId": record_id,
        "canonicalField": field.get("canonicalField"),
        "fieldHash": field.get("fieldHash"),
        "domains": deepcopy(field.get("domains") or []),
        "sourceRef": source_ref,
        "projectionHash": _canonical_hash(content),
        "content": deepcopy(content),
        "knowledgeClass": "LEGACY_PROVIDER_COMPATIBILITY_SUPPLEMENT",
        "formalSourceHashAvailable": False,
        "formalRetrievalEligible": False,
        "supplemental": True,
        "mayCreateSystemFact": False,
    }


def _experience_supplements(
    snapshot: Dict[str, Any],
    plan: Dict[str, Any],
) -> List[Dict[str, Any]]:
    by_field = {item["canonicalField"]: item for item in _arr(plan.get("fields")) if isinstance(item, dict)}
    result: List[Dict[str, Any]] = []
    positive_field = by_field.get("experience.positive.applicability")
    negative_field = by_field.get("experience.negative.risk")
    if positive_field:
        for index, card in enumerate(_arr(snapshot.get("positiveExperienceCards"))):
            if not isinstance(card, dict):
                continue
            source = _text(card.get("sourceTaskId"))
            result.append(
                _projection_record(
                    positive_field,
                    card,
                    source_ref=f"task:{source}" if source else None,
                    record_id=f"V25-A2-POS-{_text(card.get('caseId')) or index + 1}",
                )
            )
    if negative_field:
        for index, card in enumerate(_arr(snapshot.get("negativeCases"))):
            if not isinstance(card, dict):
                continue
            source = _text(card.get("sourceTaskId"))
            result.append(
                _projection_record(
                    negative_field,
                    card,
                    source_ref=f"task:{source}" if source else None,
                    record_id=f"V25-A2-NEG-{_text(card.get('caseId')) or index + 1}",
                )
            )
    return result


def _gaps(plan: Dict[str, Any], covered_fields: Iterable[str]) -> List[Dict[str, Any]]:
    covered = set(covered_fields)
    gaps: List[Dict[str, Any]] = []
    for item in _arr(plan.get("fields")):
        if not isinstance(item, dict):
            continue
        canonical = _text(item.get("canonicalField"))
        if canonical in covered:
            continue
        gaps.append(
            {
                "canonicalField": canonical,
                "fieldHash": item.get("fieldHash"),
                "role": item.get("role"),
                "reason": "NO_FORMAL_V25_KNOWLEDGE_RECORD_AVAILABLE",
            }
        )
    return gaps


def build_agent1_unified_knowledge_context(
    legacy_context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Agent1 loses direct historical-experience access after V25.7."""
    legacy = deepcopy(_dict(legacy_context))
    plan = compose_knowledge_plan("Agent1", {})
    dropped = len(_arr(legacy.get("experienceCards")))
    runtime_guardrails = {
        "principles": deepcopy(legacy.get("principles") or []),
        "guardrails": deepcopy(legacy.get("guardrails") or {}),
    }
    envelope = {
        "schema": "rag.agent_knowledge_envelope.v1",
        "version": V25_AGENT_KNOWLEDGE_VERSION,
        "agent": "Agent1",
        "composition": plan,
        "formalKnowledgeItems": [],
        "compatibilitySupplement": [],
        "insufficientEvidence": _gaps(plan, []),
        "legacyDirectExperienceDroppedCount": dropped,
        "legacyDirectKnowledgeReadAllowed": False,
        "mayCreateSystemFact": False,
    }
    envelope["envelopeHash"] = _canonical_hash(envelope)
    return {
        "version": V25_AGENT_KNOWLEDGE_VERSION,
        "mode": "v25_unified_agent1_knowledge_ingress",
        "principles": runtime_guardrails["principles"],
        "guardrails": runtime_guardrails["guardrails"],
        "experienceCards": [],
        "unifiedKnowledge": envelope,
        "queryFingerprint": envelope["envelopeHash"].removeprefix("sha256:"),
    }


def _agent2_context(package: Dict[str, Any]) -> Dict[str, Any]:
    agent1 = _dict(package.get("agent1OperatingJudgment"))
    lock = _dict(agent1.get("actionFamilyLock"))
    matrix = _dict(package.get("matrixDispatch"))
    family = _text(
        lock.get("selectedActionFamily")
        or agent1.get("selectedActionFamily")
        or package.get("actionFamily")
        or package.get("selectedActionFamily")
        or matrix.get("selectedActionFamily")
    )
    return {"actionFamily": family, "experienceRequired": True}


def build_agent2_unified_knowledge_context(
    package: Dict[str, Any],
    snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    plan = compose_knowledge_plan("Agent2", _agent2_context(package))
    snapshot = deepcopy(_dict(snapshot) or _dict(package.get("ragContextSnapshot")))
    compatibility = _experience_supplements(snapshot, plan)
    covered = {_text(item.get("canonicalField")) for item in compatibility}
    envelope = {
        "schema": "rag.agent_knowledge_envelope.v1",
        "version": V25_AGENT_KNOWLEDGE_VERSION,
        "agent": "Agent2",
        "composition": plan,
        "formalKnowledgeItems": [],
        "compatibilitySupplement": compatibility,
        "insufficientEvidence": _gaps(plan, covered),
        "legacyProvider": "rag_experience_cards",
        "legacyProviderBehindUnifiedAdapter": True,
        "legacyDirectKnowledgeReadAllowed": False,
        "compatibilitySupplementMayCreateSystemFact": False,
        "mayCreateSystemFact": False,
    }
    envelope["envelopeHash"] = _canonical_hash(envelope)
    return envelope


def build_agent3_unified_knowledge_context(
    package: Dict[str, Any],
    plan_payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    plan_payload = _dict(plan_payload)
    family = _text(plan_payload.get("actionFamily") or package.get("actionFamily"))
    customer_facing = family in {
        "title_image_test",
        "platform_activity",
        "activity_apply",
        "conversion_repair",
        "service_repair",
    }
    snapshot = _dict(package.get("ragContextSnapshot"))
    context = {
        "customerFacing": customer_facing,
        "historicalCaseRequired": bool(_arr(snapshot.get("approvedCaseIds"))),
        "experienceRequired": True,
    }
    composition = compose_knowledge_plan("Agent3", context)
    compatibility = _experience_supplements(snapshot, composition)
    covered = {_text(item.get("canonicalField")) for item in compatibility}
    envelope = {
        "schema": "rag.agent_knowledge_envelope.v1",
        "version": V25_AGENT_KNOWLEDGE_VERSION,
        "agent": "Agent3",
        "runtimeRole": "CURRENT_DETERMINISTIC_SOP_STAGE_KNOWLEDGE_PROJECTION",
        "composition": composition,
        "formalKnowledgeItems": [],
        "compatibilitySupplement": compatibility,
        "insufficientEvidence": _gaps(composition, covered),
        "legacyProviderBehindUnifiedAdapter": True,
        "newAgent3SemanticRuntimeIntroduced": False,
        "mayCreateSystemFact": False,
    }
    envelope["envelopeHash"] = _canonical_hash(envelope)
    return envelope


def install_v25_unified_agent_knowledge() -> None:
    """Patch only knowledge ingress references after the V22 single runtime exists."""
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import action_pack_core_v20_service as action_pack
    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_rag_context_v2028_service as rag
    from src.services import pipeline_agent1_microbatch_v20101_service as agent1_worker
    from src.services import pipeline_sop_task_pool_v2010_service as sop_worker
    from src.services import real_product_judgment_agent_v196_service as agent1
    from src.services import sop_builder_core_v20_service as sop

    original_agent1_context = agent1.build_agent1_rag_context
    original_snapshot = rag.build_agent_rag_context_snapshot
    original_compact_package = agent2._compact_package
    original_sop_builder = sop.build_sop_decision_from_package

    def agent1_context_v25() -> Dict[str, Any]:
        return build_agent1_unified_knowledge_context(original_agent1_context())

    def agent2_snapshot_v25(
        package: Dict[str, Any],
        action_pack_payload: Dict[str, Any] | None = None,
        *,
        limit: int = rag.DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        snapshot = original_snapshot(package, action_pack_payload, limit=limit)
        result = deepcopy(snapshot)
        result["unifiedKnowledge"] = build_agent2_unified_knowledge_context(package, snapshot)
        result["v25KnowledgeMigration"] = "AGENT2_PROVIDER_BEHIND_UNIFIED_ADAPTER"
        return result

    def compact_package_v25(package: Dict[str, Any]) -> Dict[str, Any]:
        compact = original_compact_package(package)
        legacy = _dict(compact.pop("ragContext", None))
        snapshot = _dict(package.get("ragContextSnapshot")) or legacy
        compact["unifiedKnowledge"] = build_agent2_unified_knowledge_context(package, snapshot)
        compact["legacyDirectRagContextRemoved"] = True
        return compact

    def sop_builder_v25(
        package: Dict[str, Any],
        data_version: str | None,
        *,
        pipeline_item_id: str | None = None,
    ) -> Dict[str, Any] | None:
        result = original_sop_builder(
            package,
            data_version,
            pipeline_item_id=pipeline_item_id,
        )
        if not isinstance(result, dict):
            return result
        plan_payload = _dict(package.get("agent2ActionPlan"))
        knowledge = build_agent3_unified_knowledge_context(package, plan_payload)
        result = deepcopy(result)
        result["unifiedKnowledge"] = knowledge
        task_plan = _dict(result.get("taskPlan"))
        task_plan["unifiedKnowledge"] = knowledge
        trace = _dict(task_plan.get("ragDecisionTrace"))
        trace.update(
            {
                "v25CompositionHash": _dict(knowledge.get("composition")).get("compositionHash"),
                "knowledgeIngress": "V25_UNIFIED_SOP_PROJECTION",
                "newAgent3SemanticRuntimeIntroduced": False,
            }
        )
        task_plan["ragDecisionTrace"] = trace
        result["taskPlan"] = task_plan
        product_package = _dict(result.get("productJudgmentPackage"))
        product_package["unifiedKnowledgeSummary"] = {
            "agent": "Agent3",
            "compositionHash": _dict(knowledge.get("composition")).get("compositionHash"),
            "envelopeHash": knowledge.get("envelopeHash"),
            "insufficientEvidenceCount": len(_arr(knowledge.get("insufficientEvidence"))),
        }
        result["productJudgmentPackage"] = product_package
        return result

    # V25.7 revokes Agent1 direct access to reviewed execution-experience cards.
    # The diagnostic runtime keeps system guardrails but the legacy DB reader is disabled.
    agent1._experience_cards = lambda limit=16: []
    agent1.build_agent1_rag_context = agent1_context_v25
    agent1_worker.build_operating_policy_context = agent1_context_v25

    rag.build_agent_rag_context_snapshot = agent2_snapshot_v25
    action_pack.build_agent_rag_context_snapshot = agent2_snapshot_v25
    agent2._compact_package = compact_package_v25

    sop.build_sop_decision_from_package = sop_builder_v25
    sop_worker.build_sop_decision_from_package = sop_builder_v25

    _INSTALLED = True


__all__ = [
    "V25_AGENT_KNOWLEDGE_VERSION",
    "compose_knowledge_plan",
    "build_agent1_unified_knowledge_context",
    "build_agent2_unified_knowledge_context",
    "build_agent3_unified_knowledge_context",
    "install_v25_unified_agent_knowledge",
]
