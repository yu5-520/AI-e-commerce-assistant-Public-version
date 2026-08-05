"""V22.5 hard semantic transport for Agent2 drafts and Agent3 SOPs.

V22.5.1 keeps the semantic schemas stable while removing duplicated Agent1
handoff content before it reaches downstream providers. Full Agent1 artifacts
remain available for audit; Agent2 and Agent3 receive only the compact IR handoff.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.services.agent_input_contract_v225_service import (
    AGENT_INPUT_CONTRACT_VERSION,
    AGENT2_DRAFT_INPUT_SCHEMA,
    AGENT2_MAX_ITEM_CHARS,
    AGENT3_MAX_ITEM_CHARS,
    AGENT3_SOP_INPUT_SCHEMA,
    assert_agent_input_envelope,
    build_projection_envelope,
    stable_json,
)
from src.services.artifact_transport_service import (
    inspect_artifact,
    resolve_artifact,
    store_artifact,
    validate_artifact,
)
from src.services.company_sop_rag_context_v225_service import build_agent3_company_context
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
AGENT_INPUT_TRANSPORT_VERSION = "22.5.1"
AGENT2_CONTEXT_DEDUP_VERSION = "22.5.1"
AGENT1_DECISION_IR_SOFT_LIMIT = 3_500
AGENT1_DECISION_IR_HARD_LIMIT = 4_500
AGENT1_JUDGMENT_WITHOUT_IR_LIMIT = 5_000
AGENT1_UNIQUE_HANDOFF_LIMIT = 8_000


class AgentInputProjectionError(ValueError):
    """Projection failed before a provider call, with field-level diagnostics."""

    def __init__(self, stage: str, audit: Dict[str, Any]) -> None:
        self.stage = stage
        self.audit = dict(audit)
        self.code = f"{stage}_input_projection_budget_exceeded"
        # Keep the historical text for log/search compatibility.
        super().__init__("agent_input_contract_invalid:['projection_item_budget_exceeded']")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _compact(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 6,
    max_list: int = 16,
    max_keys: int = 48,
) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value)
    if depth >= max_depth:
        if isinstance(value, dict):
            return {
                str(key): item
                for key, item in list(value.items())[:max_keys]
                if isinstance(item, (str, int, float, bool)) and item not in (None, "")
            }
        if isinstance(value, list):
            return [
                item
                for item in value[:max_list]
                if isinstance(item, (str, int, float, bool))
            ]
        return _text(value)
    if isinstance(value, list):
        result = []
        for item in value[:max_list]:
            compact = _compact(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                max_keys=max_keys,
            )
            if compact not in (None, "", [], {}):
                result.append(compact)
        return result
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in list(value.items())[:max_keys]:
            if key in {
                "raw",
                "payload",
                "events",
                "history",
                "providerTrace",
                "systemFacts",
                "pipelineItemEnvelope",
                "artifactRefs",
            }:
                continue
            compact = _compact(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                max_keys=max_keys,
            )
            if compact not in (None, "", [], {}):
                result[str(key)] = compact
        return result
    return _text(value)


def _remove_keys(value: Any, keys: set[str]) -> Any:
    """Recursively remove fields that are transported once at the outer handoff."""
    if isinstance(value, dict):
        return {
            str(key): _remove_keys(item, keys)
            for key, item in value.items()
            if str(key) not in keys
        }
    if isinstance(value, list):
        return [_remove_keys(item, keys) for item in value]
    return value


def _identity(source: Dict[str, Any]) -> Dict[str, Any]:
    product = _dict(source.get("productIdentity"))
    profile = _dict(source.get("profileLayer"))
    merged = {**profile, **product, **source}
    result = {
        "productId": merged.get("productId") or merged.get("product_id"),
        "storeId": merged.get("storeId") or merged.get("store_id"),
        "productTitle": merged.get("productTitle") or merged.get("title") or merged.get("shortTitle"),
        "title": merged.get("productTitle") or merged.get("title") or merged.get("shortTitle"),
        "platform": merged.get("platform"),
        "verticalCategory": merged.get("verticalCategory"),
        "categoryId": merged.get("categoryId"),
        "productRole": merged.get("productRole"),
        "lifecycleStage": merged.get("lifecycleStage"),
        "storeName": merged.get("storeName"),
        "erpProductCode": merged.get("erpProductCode"),
    }
    return {
        key: _text(value, 240)
        for key, value in result.items()
        if value not in (None, "")
    }


def _source_hash(artifact_id: str) -> str:
    metadata = inspect_artifact(artifact_id)
    return str(metadata.get("contentHash") or metadata.get("content_hash") or "")


def _resolve_source(artifact_id: str) -> Dict[str, Any]:
    if not str(artifact_id).startswith("ART-"):
        raise ValueError("agent_input_source_ref_missing")
    validation = validate_artifact(artifact_id)
    if validation.get("ok") is not True:
        raise ValueError("agent_input_source_artifact_invalid")
    value = resolve_artifact(artifact_id)
    if not isinstance(value, dict) or not value:
        raise ValueError("agent_input_source_payload_invalid")
    nested = value.get("payload")
    return dict(nested) if isinstance(nested, dict) and nested else dict(value)


def resolve_agent2_draft_source(row: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    """Resolve the exact capability Artifact used to compile Agent2 input."""
    refs = artifact_refs_from_row(row)
    source_ref = str(refs.get("capabilityRef") or "")
    source_hash = _source_hash(source_ref)
    return source_ref, source_hash, _resolve_source(source_ref)


def _compact_agent1_handoff(
    source: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Any, Dict[str, Any]]:
    """Build one non-duplicated Agent1 handoff without weakening Agent1 analysis."""
    judgment_raw = _dict(source.get("agent1OperatingJudgment"))
    decision_raw = _dict(source.get("agent1DecisionIR")) or _dict(
        judgment_raw.get("agent1DecisionIR")
    )
    diagnostic_raw = source.get("diagnosticExtensions") or decision_raw.get(
        "diagnosticExtensions"
    )

    decision = _remove_keys(_compact(decision_raw), {"diagnosticExtensions"})
    judgment = _remove_keys(
        _compact(judgment_raw),
        {"agent1DecisionIR", "diagnosticExtensions"},
    )
    diagnostic = _compact(
        diagnostic_raw,
        max_depth=5,
        max_list=10,
        max_keys=20,
    )
    audit = {
        "externalDecisionIRChars": len(stable_json(decision)),
        "judgmentWithoutEmbeddedIRChars": len(stable_json(judgment)),
        "diagnosticExtensionsChars": len(stable_json(diagnostic)) if diagnostic else 0,
    }
    audit["uniqueAgent1HandoffChars"] = len(
        stable_json(
            {
                "agent1DecisionIR": decision,
                "agent1OperatingJudgment": judgment,
                "diagnosticExtensions": diagnostic,
            }
        )
    )
    audit.update(
        decisionIRSoftLimit=AGENT1_DECISION_IR_SOFT_LIMIT,
        decisionIRHardLimit=AGENT1_DECISION_IR_HARD_LIMIT,
        judgmentWithoutIRLimit=AGENT1_JUDGMENT_WITHOUT_IR_LIMIT,
        uniqueAgent1HandoffLimit=AGENT1_UNIQUE_HANDOFF_LIMIT,
        decisionIRSoftLimitExceeded=audit["externalDecisionIRChars"]
        > AGENT1_DECISION_IR_SOFT_LIMIT,
        decisionIRHardLimitExceeded=audit["externalDecisionIRChars"]
        > AGENT1_DECISION_IR_HARD_LIMIT,
        judgmentWithoutIRLimitExceeded=audit["judgmentWithoutEmbeddedIRChars"]
        > AGENT1_JUDGMENT_WITHOUT_IR_LIMIT,
        uniqueAgent1HandoffLimitExceeded=audit["uniqueAgent1HandoffChars"]
        > AGENT1_UNIQUE_HANDOFF_LIMIT,
    )
    return decision, judgment, diagnostic, audit


def _projection_audit(
    payload: Dict[str, Any],
    *,
    stage: str,
    item_budget: int,
    handoff_audit: Dict[str, Any],
) -> Dict[str, Any]:
    total = len(stable_json(payload))
    fields: Dict[str, Dict[str, Any]] = {}
    for key, value in payload.items():
        without = {name: item for name, item in payload.items() if name != key}
        fields[str(key)] = {
            "valueChars": len(stable_json(value)),
            "marginalChars": total - len(stable_json(without)),
        }
    largest = max(
        fields,
        key=lambda key: int(fields[key].get("marginalChars") or 0),
        default=None,
    )
    return {
        "stage": stage,
        "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
        "contextDedupVersion": AGENT2_CONTEXT_DEDUP_VERSION,
        "projectedChars": total,
        "itemCharBudget": item_budget,
        "overByChars": max(0, total - item_budget),
        "budgetStatus": "passed" if total <= item_budget else "exceeded",
        "fieldChars": fields,
        "largestField": largest,
        "largestFieldMarginalChars": (
            int(fields[largest].get("marginalChars") or 0) if largest else 0
        ),
        "transportDeduplicated": True,
        "deduplicationPolicy": [
            "single_outer_agent1DecisionIR",
            "agent1OperatingJudgment_without_embedded_decision_ir",
            "single_outer_diagnosticExtensions",
            "lockedActionFamily_only",
            "single_top_level_productTitle",
        ],
        "agent1Handoff": dict(handoff_audit),
    }


def _finalize_projection(
    *,
    schema: str,
    stage: str,
    payload: Dict[str, Any],
    source_ref: str,
    source_content_hash: str,
    item_budget: int,
    handoff_audit: Dict[str, Any],
) -> Dict[str, Any]:
    audit = _projection_audit(
        payload,
        stage=stage,
        item_budget=item_budget,
        handoff_audit=handoff_audit,
    )
    if int(audit["projectedChars"]) > item_budget:
        raise AgentInputProjectionError(stage, audit)
    envelope = build_projection_envelope(
        schema=schema,
        payload=payload,
        source_artifact_refs=[source_ref],
        source_content_hash=source_content_hash,
    )
    _dict(envelope.get("projectionAudit")).update(audit)
    return envelope


def _store(
    envelope: Dict[str, Any],
    *,
    row: Dict[str, Any],
    source_ref: str,
    ref_key: str,
    schema: str,
) -> str:
    artifact = store_artifact(
        artifact_type=schema,
        value=envelope,
        schema_version=AGENT_INPUT_CONTRACT_VERSION,
        tenant_id=row.get("tenant_id"),
        store_id=row.get("store_id"),
        product_id=row.get("product_id"),
        data_version=row.get("data_version"),
        created_by="agent_input_transport_v225",
        parent_refs=[source_ref],
        metadata={
            "pipelineItemId": row.get("item_id"),
            "hardInterface": True,
            "fallbackAllowed": False,
            "sourceArtifactRef": source_ref,
            "sourceContentHash": envelope.get("sourceContentHash"),
            "projectedContentHash": envelope.get("projectedContentHash"),
            "projectedChars": _dict(envelope.get("projectionAudit")).get("projectedChars"),
            "estimatedTokens": _dict(envelope.get("projectionAudit")).get("estimatedTokens"),
            "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
            "transportDeduplicated": True,
        },
    )
    artifact_id = str(artifact["artifactId"])
    attach_pipeline_artifact_ref(
        str(row.get("item_id")),
        ref_key,
        artifact_id,
        make_current=True,
    )
    return artifact_id


def _existing(
    row: Dict[str, Any],
    *,
    ref_key: str,
    schema: str,
    source_ref: str,
    source_hash: str,
) -> str | None:
    refs = artifact_refs_from_row(row)
    artifact_id = str(refs.get(ref_key) or "")
    if not artifact_id.startswith("ART-"):
        return None
    if validate_artifact(artifact_id, expected_type=schema).get("ok") is not True:
        return None
    try:
        value = resolve_artifact(artifact_id)
        assert_agent_input_envelope(value, expected_schema=schema)
    except Exception:
        return None
    if source_ref not in _arr(value.get("sourceArtifactRefs")):
        return None
    if str(value.get("sourceContentHash") or "") != source_hash:
        return None
    audit = _dict(value.get("projectionAudit"))
    if audit.get("transportDeduplicated") is not True:
        return None
    if str(audit.get("transportVersion") or "") != AGENT_INPUT_TRANSPORT_VERSION:
        return None
    return artifact_id


def compile_agent2_draft_envelope(
    source: Dict[str, Any],
    *,
    source_ref: str,
    source_content_hash: str,
) -> Dict[str, Any]:
    identity = _identity(source)
    decision_ir, judgment, diagnostic, handoff_audit = _compact_agent1_handoff(source)
    matrix = _dict(source.get("matrixDispatch"))
    family_lock = _dict(_dict(source.get("agent1OperatingJudgment")).get("actionFamilyLock"))
    family = _text(
        source.get("lockedActionFamily")
        or source.get("actionFamily")
        or matrix.get("selectedActionFamily")
        or family_lock.get("selectedActionFamily"),
        100,
    )
    package_id = _text(source.get("packageId") or source.get("itemId"), 220)
    product_id = _text(source.get("productId") or identity.get("productId"), 160)
    store_id = _text(source.get("storeId") or identity.get("storeId"), 160)
    if not package_id or not product_id or not store_id or not family:
        raise ValueError("agent2_draft_identity_or_family_missing")
    payload = {
        "packageId": package_id,
        "itemId": source.get("itemId"),
        "dataVersion": source.get("dataVersion"),
        "productId": product_id,
        "storeId": store_id,
        "productTitle": identity.get("productTitle") or identity.get("title"),
        "productIdentity": identity,
        "agent1DecisionIR": decision_ir,
        "agent1OperatingJudgment": judgment,
        "matrixDispatch": _compact(matrix, max_depth=4, max_list=10, max_keys=24),
        "lockedActionFamily": family,
        "actionParameterPack": _compact(
            source.get("actionParameterPack"), max_depth=7, max_list=20, max_keys=56
        ),
        "recentFiveOrLatestFacts": _compact(
            source.get("recentFiveOrLatestFacts"), max_depth=5, max_list=14, max_keys=24
        ),
        "verticalActionRag": _compact(
            source.get("ragContextSnapshot"), max_depth=6, max_list=12, max_keys=28
        ),
        "diagnosticExtensionContract": _compact(
            source.get("diagnosticExtensionContract"), max_depth=4, max_list=8, max_keys=18
        ),
        "diagnosticExtensions": diagnostic,
        "inputContract": {
            "schema": AGENT2_DRAFT_INPUT_SCHEMA,
            "version": AGENT_INPUT_CONTRACT_VERSION,
            "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
            "sourceRef": source_ref,
            "sourceContentHash": source_content_hash,
            "fallbackAllowed": False,
            "fullCapabilityReadAllowed": False,
            "finalSopGenerationAllowed": False,
            "agent1FullArtifactAuditOnly": True,
            "transportDeduplicated": True,
        },
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    return _finalize_projection(
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        stage="agent2_draft",
        payload=payload,
        source_ref=source_ref,
        source_content_hash=source_content_hash,
        item_budget=AGENT2_MAX_ITEM_CHARS,
        handoff_audit=handoff_audit,
    )


def compile_agent3_sop_envelope(
    source: Dict[str, Any],
    *,
    source_ref: str,
    source_content_hash: str,
) -> Dict[str, Any]:
    identity = _identity(source)
    decision_ir, judgment, _diagnostic, handoff_audit = _compact_agent1_handoff(source)
    matrix = _dict(source.get("matrixDispatch"))
    draft = _dict(source.get("agent2ActionDraft"))
    proof = _dict(source.get("agent2DraftExecutionProof"))
    family = _text(
        source.get("lockedActionFamily")
        or source.get("actionFamily")
        or draft.get("actionFamily")
        or matrix.get("selectedActionFamily"),
        100,
    )
    package_id = _text(
        source.get("packageId") or draft.get("packageId") or source.get("itemId"), 220
    )
    product_id = _text(source.get("productId") or identity.get("productId"), 160)
    store_id = _text(source.get("storeId") or identity.get("storeId"), 160)
    if not package_id or not product_id or not store_id or not family or not draft:
        raise ValueError("agent3_sop_identity_family_or_draft_missing")
    company = build_agent3_company_context(source)
    payload = {
        "packageId": package_id,
        "itemId": source.get("itemId"),
        "dataVersion": source.get("dataVersion"),
        "productId": product_id,
        "storeId": store_id,
        "productTitle": identity.get("productTitle") or identity.get("title"),
        "productIdentity": identity,
        "agent1DecisionIR": decision_ir,
        "agent1OperatingJudgment": judgment,
        "matrixDispatch": _compact(matrix, max_depth=4, max_list=10, max_keys=24),
        "lockedActionFamily": family,
        "actionParameterPack": _compact(
            source.get("actionParameterPack"), max_depth=7, max_list=20, max_keys=56
        ),
        "recentFiveOrLatestFacts": _compact(
            source.get("recentFiveOrLatestFacts"), max_depth=5, max_list=14, max_keys=24
        ),
        "agent2ActionDraft": _compact(draft, max_depth=8, max_list=24, max_keys=64),
        "agent2DraftExecutionProof": _compact(proof, max_depth=4, max_list=8, max_keys=24),
        **company,
        "inputContract": {
            "schema": AGENT3_SOP_INPUT_SCHEMA,
            "version": AGENT_INPUT_CONTRACT_VERSION,
            "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
            "sourceRef": source_ref,
            "sourceContentHash": source_content_hash,
            "fallbackAllowed": False,
            "fullUpstreamArtifactReadAllowed": False,
            "actionFamilyMutationAllowed": False,
            "numericBoundaryExpansionAllowed": False,
            "agent1FullArtifactAuditOnly": True,
            "transportDeduplicated": True,
        },
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "", [], {})}
    return _finalize_projection(
        schema=AGENT3_SOP_INPUT_SCHEMA,
        stage="agent3_sop",
        payload=payload,
        source_ref=source_ref,
        source_content_hash=source_content_hash,
        item_budget=AGENT3_MAX_ITEM_CHARS,
        handoff_audit=handoff_audit,
    )


def ensure_agent2_draft_input_ref(row: Dict[str, Any]) -> str:
    source_ref, source_hash, source = resolve_agent2_draft_source(row)
    existing = _existing(
        row,
        ref_key="agent2DraftInputRef",
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        source_ref=source_ref,
        source_hash=source_hash,
    )
    if existing:
        attach_pipeline_artifact_ref(
            str(row.get("item_id")), "agent2DraftInputRef", existing, make_current=True
        )
        return existing
    envelope = compile_agent2_draft_envelope(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
    )
    return _store(
        envelope,
        row=row,
        source_ref=source_ref,
        ref_key="agent2DraftInputRef",
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
    )


def ensure_agent3_sop_input_ref(row: Dict[str, Any]) -> str:
    refs = artifact_refs_from_row(row)
    source_ref = str(
        refs.get("agent2DraftRef")
        or refs.get("currentStageRef")
        or row.get("payload_artifact_ref")
        or ""
    )
    source_hash = _source_hash(source_ref)
    existing = _existing(
        row,
        ref_key="agent3SopInputRef",
        schema=AGENT3_SOP_INPUT_SCHEMA,
        source_ref=source_ref,
        source_hash=source_hash,
    )
    if existing:
        attach_pipeline_artifact_ref(
            str(row.get("item_id")), "agent3SopInputRef", existing, make_current=True
        )
        return existing
    source = _resolve_source(source_ref)
    envelope = compile_agent3_sop_envelope(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
    )
    return _store(
        envelope,
        row=row,
        source_ref=source_ref,
        ref_key="agent3SopInputRef",
        schema=AGENT3_SOP_INPUT_SCHEMA,
    )


def resolve_agent_input_ref(artifact_id: str, *, expected_schema: str) -> Dict[str, Any]:
    if validate_artifact(artifact_id, expected_type=expected_schema).get("ok") is not True:
        raise ValueError("agent_input_artifact_invalid")
    value = resolve_artifact(artifact_id)
    assert_agent_input_envelope(value, expected_schema=expected_schema)
    return value


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_INPUT_TRANSPORT_VERSION",
    "AGENT2_CONTEXT_DEDUP_VERSION",
    "AgentInputProjectionError",
    "compile_agent2_draft_envelope",
    "compile_agent3_sop_envelope",
    "ensure_agent2_draft_input_ref",
    "ensure_agent3_sop_input_ref",
    "resolve_agent2_draft_source",
    "resolve_agent_input_ref",
]
