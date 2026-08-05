"""V22.5.14 Agent2 action-evidence transport.

Agent1 keeps the complete report and diagnosis Artifacts for audit. Agent2 receives
only the locked action, strongly related trend facts, execution boundaries and
lineage references. No provider-facing full report, raw Agent1 output or duplicated
lineage tree is transported.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from src.services import agent_input_transport_v225_service as legacy
from src.services.agent_execution_lock_v2255_service import execution_lock_from
from src.services.agent_input_contract_v225_service import (
    AGENT_INPUT_CONTRACT_VERSION,
    AGENT2_DRAFT_INPUT_SCHEMA,
    AGENT2_MAX_ITEM_CHARS,
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
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.5"
AGENT_INPUT_TRANSPORT_VERSION = "22.5.14"
AGENT2_CONTEXT_DEDUP_VERSION = "22.5.14"
AGENT2_EVIDENCE_SLICE_VERSION = "22.5.14"

_FAMILY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "title_image_test": (
        "ctr",
        "click",
        "点击",
        "impression",
        "exposure",
        "曝光",
        "conversion",
        "cvr",
        "转化",
        "title",
        "标题",
        "image",
        "主图",
        "creative",
        "素材",
    ),
    "roas_scale": (
        "roi",
        "roas",
        "投产",
        "spend",
        "cost",
        "消耗",
        "gmv",
        "成交",
        "conversion",
        "cvr",
        "转化",
        "budget",
        "预算",
        "bid",
        "出价",
        "profit",
        "margin",
        "利润",
        "毛利",
    ),
    "roas_guard": (
        "roi",
        "roas",
        "投产",
        "spend",
        "cost",
        "消耗",
        "gmv",
        "成交",
        "conversion",
        "cvr",
        "转化",
        "budget",
        "预算",
        "bid",
        "出价",
        "profit",
        "margin",
        "利润",
        "毛利",
    ),
    "platform_activity": (
        "traffic",
        "流量",
        "gmv",
        "成交",
        "sales",
        "销量",
        "conversion",
        "转化",
        "profit",
        "margin",
        "利润",
        "毛利",
        "activity",
        "活动",
    ),
    "activity_apply": (
        "traffic",
        "流量",
        "gmv",
        "成交",
        "sales",
        "销量",
        "conversion",
        "转化",
        "profit",
        "margin",
        "利润",
        "毛利",
        "activity",
        "活动",
    ),
    "conversion_repair": (
        "conversion",
        "cvr",
        "转化",
        "refund",
        "退款",
        "after_sale",
        "售后",
        "detail",
        "详情",
        "service",
        "客服",
    ),
    "service_repair": (
        "conversion",
        "cvr",
        "转化",
        "refund",
        "退款",
        "after_sale",
        "售后",
        "service",
        "客服",
    ),
    "similar_product_test": (
        "similar",
        "相似",
        "comparison",
        "对照",
        "ctr",
        "点击",
        "conversion",
        "转化",
        "gmv",
        "成交",
    ),
}

_BOUNDARY_MARKERS = (
    "permission",
    "authority",
    "approval",
    "权限",
    "审批",
    "budget",
    "预算",
    "parameter",
    "参数",
    "limit",
    "threshold",
    "边界",
    "上限",
    "下限",
    "rollback",
    "回滚",
    "stop",
    "停止",
    "review",
    "复盘",
    "window",
    "周期",
    "target",
    "目标",
    "current",
    "当前",
)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _canonical(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", _text(value, 300).lower()).strip("_")


def _family_aliases(family: str) -> Tuple[str, ...]:
    canonical = _canonical(family)
    if canonical in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[canonical]
    return tuple(dict.fromkeys((*_BOUNDARY_MARKERS, canonical)))


def _contains_marker(value: Any, markers: Iterable[str]) -> bool:
    haystack = stable_json(value).lower()
    return any(str(marker).lower() in haystack for marker in markers if str(marker))


def _compact(value: Any, *, depth: int = 0, max_depth: int = 5, max_list: int = 12, max_keys: int = 32) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value, 500)
    if depth >= max_depth:
        if isinstance(value, dict):
            return {
                str(key): item
                for key, item in list(value.items())[:max_keys]
                if isinstance(item, (str, int, float, bool)) and item not in (None, "")
            }
        if isinstance(value, list):
            return [item for item in value[:max_list] if isinstance(item, (str, int, float, bool))]
        return _text(value, 500)
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
            if str(key) in {
                "raw",
                "payload",
                "events",
                "history",
                "providerTrace",
                "systemFacts",
                "signalEvidence",
                "sourceLineageValidation",
                "artifactRefs",
                "rawAgent1Judgment",
                "recoveredAgent1Judgment",
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
    return _text(value, 500)


def _identity(source: Dict[str, Any]) -> Dict[str, Any]:
    return legacy._identity(source)


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
    refs = artifact_refs_from_row(row)
    source_ref = str(refs.get("capabilityRef") or "")
    return source_ref, _source_hash(source_ref), _resolve_source(source_ref)


def _execution_lock(source: Dict[str, Any]) -> Dict[str, Any]:
    lock = execution_lock_from(source)
    return {
        key: value
        for key, value in lock.items()
        if key
        in {
            "version",
            "hotfixVersion",
            "decisionType",
            "locked",
            "evidenceStatus",
            "evidenceBasis",
            "riskClass",
            "reversibleTest",
            "reviewRequired",
            "rollbackRequired",
            "selectedOperatingRoute",
            "selectedActionFamily",
            "primaryProblemNode",
            "primaryAction",
            "primaryExecutionTarget",
            "primaryOwner",
            "decisiveFacts",
            "supportingCoordination",
            "forbiddenActionDomains",
            "advisoryMissingEvidence",
            "hardEvidenceBlockers",
        }
        and value not in (None, "", [], {})
    }


def _decisive_facts(source: Dict[str, Any], lock: Dict[str, Any]) -> List[Any]:
    judgment = _dict(source.get("agent1OperatingJudgment"))
    decision = _dict(source.get("agent1DecisionIR")) or _dict(judgment.get("agent1DecisionIR"))
    values = (
        _arr(lock.get("decisiveFacts"))
        or _arr(source.get("decisiveFacts"))
        or _arr(judgment.get("decisiveFacts"))
        or _arr(decision.get("decisiveFacts"))
    )
    result: List[Any] = []
    for value in values[:10]:
        compact = _compact(value, max_depth=3, max_list=6, max_keys=16)
        if compact not in (None, "", [], {}) and compact not in result:
            result.append(compact)
    return result


def _relevant_recent_facts(source: Dict[str, Any], family: str, decisive: List[Any]) -> List[Any]:
    aliases = _family_aliases(family)
    candidates: List[Any] = []
    for key in (
        "recentFiveOrLatestFacts",
        "trendFacts",
        "metricDigest",
        "timeSeriesFeatures",
    ):
        value = source.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.extend(value.values())
    judgment = _dict(source.get("agent1OperatingJudgment"))
    decision = _dict(source.get("agent1DecisionIR")) or _dict(judgment.get("agent1DecisionIR"))
    for value in (
        judgment.get("recentFiveOrLatestFacts"),
        decision.get("recentFiveOrLatestFacts"),
        decision.get("trendFacts"),
        decision.get("metricDigest"),
    ):
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.extend(value.values())

    result: List[Any] = []
    for value in candidates:
        if not _contains_marker(value, aliases):
            continue
        compact = _compact(value, max_depth=4, max_list=8, max_keys=20)
        if compact not in (None, "", [], {}) and compact not in result:
            result.append(compact)
        if len(result) >= 12:
            break
    if not result:
        result = [{"decisiveFact": value} for value in decisive[:8]]
    return result[:12]


def _parameter_slice(source: Dict[str, Any], family: str) -> Dict[str, Any]:
    pack = _dict(source.get("actionParameterPack"))
    aliases = tuple(dict.fromkeys((*_family_aliases(family), *_BOUNDARY_MARKERS)))
    result: Dict[str, Any] = {"lockedActionFamily": family}
    for key, value in pack.items():
        if key in {
            "ragContextSummary",
            "fullMetricEvidence",
            "systemFacts",
            "signalEvidence",
            "sourceLineageValidation",
            "rawAgent1Judgment",
            "recoveredAgent1Judgment",
        }:
            continue
        if key in {
            "permissionBounds",
            "parameterBounds",
            "inventoryCoordination",
            "trafficSourceSummary",
            "rollbackBoundary",
            "reviewWindow",
            "stopConditions",
        } or _contains_marker({key: value}, aliases):
            compact = _compact(value, max_depth=5, max_list=10, max_keys=28)
            if compact not in (None, "", [], {}):
                result[str(key)] = compact
    return result


def _lineage_summary(source: Dict[str, Any], source_ref: str, source_hash: str) -> Dict[str, Any]:
    candidates = [
        source.get("sourceLineageValidation"),
        _dict(source.get("signalEvidence")).get("sourceLineageValidation"),
        _dict(source.get("signal")).get("sourceLineageValidation"),
        _dict(source.get("systemFacts")).get("sourceLineageValidation"),
    ]
    lineage = next((_dict(value) for value in candidates if isinstance(value, dict) and value), {})
    result = {
        "sourceArtifactRefs": [source_ref],
        "sourceContentHash": source_hash,
        "sourceIdentityComplete": lineage.get("sourceIdentityComplete"),
        "status": lineage.get("status"),
        "sourceVersionCount": lineage.get("sourceVersionCount"),
        "sourceDatasetCount": lineage.get("sourceDatasetCount"),
        "businessDateCount": lineage.get("businessDateCount"),
        "businessDates": lineage.get("businessDates"),
        "contentHashVerified": lineage.get("contentHashVerified"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _minimal_judgment(source: Dict[str, Any], lock: Dict[str, Any], evidence_slice: Dict[str, Any]) -> Dict[str, Any]:
    judgment = _dict(source.get("agent1OperatingJudgment"))
    result = {
        "decisionType": source.get("decisionType") or judgment.get("decisionType") or lock.get("decisionType"),
        "decisionSummary": judgment.get("decisionSummary") or source.get("decisionSummary") or source.get("finding"),
        "selectedOperatingRoute": lock.get("selectedOperatingRoute"),
        "selectedActionFamilyHint": lock.get("selectedActionFamily"),
        "primaryProblemNode": lock.get("primaryProblemNode"),
        "primaryAction": lock.get("primaryAction"),
        "primaryExecutionTarget": lock.get("primaryExecutionTarget"),
        "primaryOwner": lock.get("primaryOwner"),
        "decisiveFacts": evidence_slice.get("decisiveFacts"),
        "executionLock": lock,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _minimal_decision_ir(source: Dict[str, Any], lock: Dict[str, Any], evidence_slice: Dict[str, Any]) -> Dict[str, Any]:
    judgment = _dict(source.get("agent1OperatingJudgment"))
    decision = _dict(source.get("agent1DecisionIR")) or _dict(judgment.get("agent1DecisionIR"))
    result = {
        "decisionType": source.get("decisionType") or decision.get("decisionType") or lock.get("decisionType"),
        "decisionSummary": decision.get("decisionSummary") or judgment.get("decisionSummary") or source.get("finding"),
        "selectedOperatingRoute": lock.get("selectedOperatingRoute"),
        "selectedActionFamily": lock.get("selectedActionFamily"),
        "primaryProblemNode": lock.get("primaryProblemNode"),
        "primaryAction": lock.get("primaryAction"),
        "primaryExecutionTarget": lock.get("primaryExecutionTarget"),
        "primaryOwner": lock.get("primaryOwner"),
        "evidenceSlice": evidence_slice,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _matrix_slice(source: Dict[str, Any], lock: Dict[str, Any]) -> Dict[str, Any]:
    matrix = _dict(source.get("matrixDispatch"))
    result = {
        "selectedOperatingRoute": lock.get("selectedOperatingRoute") or matrix.get("selectedOperatingRoute"),
        "selectedActionFamily": lock.get("selectedActionFamily") or matrix.get("selectedActionFamily"),
        "selectedPrimaryAction": lock.get("primaryAction") or matrix.get("selectedPrimaryAction"),
        "selectedExecutionTarget": lock.get("primaryExecutionTarget") or matrix.get("selectedExecutionTarget"),
        "selectedOwner": lock.get("primaryOwner") or matrix.get("selectedOwner"),
        "dispatchStatus": matrix.get("dispatchStatus") or "locked",
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _rag_slice(source: Dict[str, Any]) -> Dict[str, Any]:
    rag = _dict(source.get("ragContextSnapshot"))
    result = {
        "version": rag.get("version"),
        "status": rag.get("status"),
        "queryFingerprint": rag.get("queryFingerprint"),
        "matchedCount": rag.get("matchedCount"),
        "approvedCaseIds": _arr(rag.get("approvedCaseIds"))[:8],
        "agentInstruction": _text(rag.get("agentInstruction"), 900),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _projection_audit(payload: Dict[str, Any], *, source_ref: str) -> Dict[str, Any]:
    total = len(stable_json(payload))
    fields = {
        str(key): {"valueChars": len(stable_json(value))}
        for key, value in payload.items()
    }
    largest = max(fields, key=lambda key: int(fields[key]["valueChars"]), default=None)
    return {
        "stage": "agent2_draft",
        "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
        "contextDedupVersion": AGENT2_CONTEXT_DEDUP_VERSION,
        "evidenceSliceVersion": AGENT2_EVIDENCE_SLICE_VERSION,
        "projectedChars": total,
        "itemCharBudget": AGENT2_MAX_ITEM_CHARS,
        "overByChars": max(0, total - AGENT2_MAX_ITEM_CHARS),
        "budgetStatus": "passed" if total <= AGENT2_MAX_ITEM_CHARS else "exceeded",
        "fieldChars": fields,
        "largestField": largest,
        "sourceRef": source_ref,
        "transportDeduplicated": True,
        "fullReportExcluded": True,
        "rawAgent1OutputExcluded": True,
        "fullAuditArtifactExcluded": True,
        "lineageReferenceOnly": True,
        "deduplicationPolicy": [
            "execution_lock_once",
            "strongly_related_metrics_only",
            "recent_trend_slice_only",
            "permission_parameter_rollback_boundaries_only",
            "source_ref_hash_summary_only",
        ],
    }


def _fit_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(payload)
    if len(stable_json(result)) <= AGENT2_MAX_ITEM_CHARS:
        return result
    recent = _arr(result.get("recentFiveOrLatestFacts"))
    result["recentFiveOrLatestFacts"] = recent[:8]
    decision = _dict(result.get("agent1DecisionIR"))
    evidence = _dict(decision.get("evidenceSlice"))
    evidence["trendMetrics"] = _arr(evidence.get("trendMetrics"))[:8]
    evidence["decisiveFacts"] = _arr(evidence.get("decisiveFacts"))[:6]
    decision["evidenceSlice"] = evidence
    result["agent1DecisionIR"] = decision
    rag = _dict(result.get("verticalActionRag"))
    rag["approvedCaseIds"] = _arr(rag.get("approvedCaseIds"))[:4]
    rag["agentInstruction"] = _text(rag.get("agentInstruction"), 500)
    result["verticalActionRag"] = rag
    if len(stable_json(result)) <= AGENT2_MAX_ITEM_CHARS:
        return result
    result.pop("verticalActionRag", None)
    result.pop("productIdentity", None)
    if len(stable_json(result)) <= AGENT2_MAX_ITEM_CHARS:
        return result
    raise legacy.AgentInputProjectionError(
        "agent2_draft",
        _projection_audit(result, source_ref="budget_fit_failed"),
    )


def compile_agent2_draft_envelope(
    source: Dict[str, Any],
    *,
    source_ref: str,
    source_content_hash: str,
) -> Dict[str, Any]:
    identity = _identity(source)
    lock = _execution_lock(source)
    family = _text(
        lock.get("selectedActionFamily")
        or source.get("lockedActionFamily")
        or source.get("actionFamily"),
        100,
    )
    package_id = _text(source.get("packageId") or source.get("itemId"), 220)
    product_id = _text(source.get("productId") or identity.get("productId"), 160)
    store_id = _text(source.get("storeId") or identity.get("storeId"), 160)
    if not package_id or not product_id or not store_id or not family:
        raise ValueError("agent2_draft_identity_or_family_missing")

    decisive = _decisive_facts(source, lock)
    recent = _relevant_recent_facts(source, family, decisive)
    parameters = _parameter_slice(source, family)
    lineage = _lineage_summary(source, source_ref, source_content_hash)
    evidence_slice = {
        "version": AGENT2_EVIDENCE_SLICE_VERSION,
        "actionFamily": family,
        "decisiveFacts": decisive,
        "trendMetrics": recent,
        "constraints": {
            key: parameters.get(key)
            for key in (
                "permissionBounds",
                "parameterBounds",
                "rollbackBoundary",
                "reviewWindow",
                "stopConditions",
                "inventoryCoordination",
            )
            if parameters.get(key) not in (None, "", [], {})
        },
        "lineageRefs": lineage,
        "fullReportExcluded": True,
        "agent1AuditArtifactReferenceOnly": True,
    }
    payload = {
        "packageId": package_id,
        "itemId": source.get("itemId"),
        "dataVersion": source.get("dataVersion"),
        "productId": product_id,
        "storeId": store_id,
        "productTitle": identity.get("productTitle") or identity.get("title"),
        "productIdentity": identity,
        "agent1DecisionIR": _minimal_decision_ir(source, lock, evidence_slice),
        "agent1OperatingJudgment": _minimal_judgment(source, lock, evidence_slice),
        "matrixDispatch": _matrix_slice(source, lock),
        "lockedActionFamily": family,
        "actionParameterPack": parameters,
        "recentFiveOrLatestFacts": recent,
        "verticalActionRag": _rag_slice(source),
        "inputContract": {
            "schema": AGENT2_DRAFT_INPUT_SCHEMA,
            "version": AGENT_INPUT_CONTRACT_VERSION,
            "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
            "evidenceSliceVersion": AGENT2_EVIDENCE_SLICE_VERSION,
            "sourceRef": source_ref,
            "sourceContentHash": source_content_hash,
            "lineageRefs": lineage,
            "fallbackAllowed": False,
            "fullCapabilityReadAllowed": False,
            "fullReportReadAllowed": False,
            "rawAgent1OutputReadAllowed": False,
            "finalSopGenerationAllowed": False,
            "agent1FullArtifactAuditOnly": True,
            "transportDeduplicated": True,
        },
    }
    payload = {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }
    payload = _fit_payload(payload)
    audit = _projection_audit(payload, source_ref=source_ref)
    if int(audit["projectedChars"]) > AGENT2_MAX_ITEM_CHARS:
        raise legacy.AgentInputProjectionError("agent2_draft", audit)
    envelope = build_projection_envelope(
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        payload=payload,
        source_artifact_refs=[source_ref],
        source_content_hash=source_content_hash,
    )
    _dict(envelope.get("projectionAudit")).update(audit)
    return envelope


def _existing(
    row: Dict[str, Any],
    *,
    source_ref: str,
    source_hash: str,
) -> str | None:
    refs = artifact_refs_from_row(row)
    artifact_id = str(refs.get("agent2DraftInputRef") or "")
    if not artifact_id.startswith("ART-"):
        return None
    if validate_artifact(artifact_id, expected_type=AGENT2_DRAFT_INPUT_SCHEMA).get("ok") is not True:
        return None
    try:
        value = resolve_artifact(artifact_id)
        assert_agent_input_envelope(value, expected_schema=AGENT2_DRAFT_INPUT_SCHEMA)
    except Exception:
        return None
    if source_ref not in _arr(value.get("sourceArtifactRefs")):
        return None
    if str(value.get("sourceContentHash") or "") != source_hash:
        return None
    audit = _dict(value.get("projectionAudit"))
    if str(audit.get("transportVersion") or "") != AGENT_INPUT_TRANSPORT_VERSION:
        return None
    if str(audit.get("evidenceSliceVersion") or "") != AGENT2_EVIDENCE_SLICE_VERSION:
        return None
    if audit.get("fullReportExcluded") is not True:
        return None
    return artifact_id


def _store(
    envelope: Dict[str, Any],
    *,
    row: Dict[str, Any],
    source_ref: str,
) -> str:
    artifact = store_artifact(
        artifact_type=AGENT2_DRAFT_INPUT_SCHEMA,
        value=envelope,
        schema_version=AGENT_INPUT_CONTRACT_VERSION,
        tenant_id=row.get("tenant_id"),
        store_id=row.get("store_id"),
        product_id=row.get("product_id"),
        data_version=row.get("data_version"),
        created_by="agent_input_transport_v22514",
        parent_refs=[source_ref],
        metadata={
            "pipelineItemId": row.get("item_id"),
            "hardInterface": True,
            "fallbackAllowed": False,
            "sourceArtifactRef": source_ref,
            "sourceContentHash": envelope.get("sourceContentHash"),
            "projectedContentHash": envelope.get("projectedContentHash"),
            "projectedChars": _dict(envelope.get("projectionAudit")).get("projectedChars"),
            "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
            "evidenceSliceVersion": AGENT2_EVIDENCE_SLICE_VERSION,
            "fullReportExcluded": True,
            "transportDeduplicated": True,
        },
    )
    artifact_id = str(artifact["artifactId"])
    attach_pipeline_artifact_ref(
        str(row.get("item_id")),
        "agent2DraftInputRef",
        artifact_id,
        make_current=True,
    )
    return artifact_id


def ensure_agent2_draft_input_ref(row: Dict[str, Any]) -> str:
    source_ref, source_hash, source = resolve_agent2_draft_source(row)
    existing = _existing(row, source_ref=source_ref, source_hash=source_hash)
    if existing:
        attach_pipeline_artifact_ref(
            str(row.get("item_id")),
            "agent2DraftInputRef",
            existing,
            make_current=True,
        )
        return existing
    envelope = compile_agent2_draft_envelope(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
    )
    return _store(envelope, row=row, source_ref=source_ref)


def resolve_agent_input_ref(artifact_id: str, *, expected_schema: str) -> Dict[str, Any]:
    return legacy.resolve_agent_input_ref(artifact_id, expected_schema=expected_schema)


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_INPUT_TRANSPORT_VERSION",
    "AGENT2_CONTEXT_DEDUP_VERSION",
    "AGENT2_EVIDENCE_SLICE_VERSION",
    "compile_agent2_draft_envelope",
    "ensure_agent2_draft_input_ref",
    "resolve_agent2_draft_source",
    "resolve_agent_input_ref",
]
